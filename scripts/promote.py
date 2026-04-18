"""
Promote dev DB state → prod DB.

Copies all providers, models, and voices from the dev database to prod.
Dev DB is the reviewed source of truth — prod mirrors it exactly.
No AI parsing — pure data copy, zero token cost.

Two-step workflow designed for Claude Code:
  Step 1 — dry-run (default): show what would change in prod, no write
  Step 2 — apply: write to prod DB

Usage:
    uv run python scripts/promote.py                  # dry-run: show diff only
    uv run python scripts/promote.py --apply          # promote all to prod
    uv run python scripts/promote.py cartesia --apply # promote one provider

Requirements:
    PROD_DATABASE_URL must be set in .env
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from naaviq.config import settings
from naaviq.models import Model, Provider, Voice


@dataclass
class SectionStats:
    added: int = 0
    updated: int = 0

    def __str__(self) -> str:
        return f"+{self.added}  ~{self.updated}"

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.updated)


async def _promote_provider(
    provider_id: str,
    dev_session: AsyncSession,
    prod_session: AsyncSession,
) -> None:
    result = await dev_session.execute(
        select(Provider).where(Provider.provider_id == provider_id)
    )
    dev_provider = result.scalar_one_or_none()
    if not dev_provider:
        return

    result = await prod_session.execute(
        select(Provider).where(Provider.provider_id == provider_id)
    )
    prod_provider = result.scalar_one_or_none()

    fields = ["display_name", "type", "website", "description", "source",
              "last_synced_at", "deprecated_at"]

    if prod_provider:
        for f in fields:
            setattr(prod_provider, f, getattr(dev_provider, f))
    else:
        prod_session.add(Provider(**{
            "provider_id": provider_id,
            **{f: getattr(dev_provider, f) for f in fields},
        }))
    await prod_session.flush()


async def _promote_models(
    provider_id: str,
    type_: str,
    dev_session: AsyncSession,
    prod_session: AsyncSession,
) -> SectionStats:
    dev_result = await dev_session.execute(
        select(Model)
        .where(Model.provider_id == provider_id)
        .where(Model.type == type_)
    )
    dev_models = {m.model_id: m for m in dev_result.scalars().all()}

    prod_result = await prod_session.execute(
        select(Model)
        .where(Model.provider_id == provider_id)
        .where(Model.type == type_)
    )
    prod_models = {m.model_id: m for m in prod_result.scalars().all()}

    fields = ["display_name", "type", "languages", "streaming", "is_default",
              "description", "eol_date", "meta", "deprecated_at"]
    stats = SectionStats()

    for model_id, dev_m in dev_models.items():
        if model_id in prod_models:
            for f in fields:
                setattr(prod_models[model_id], f, getattr(dev_m, f))
            stats.updated += 1
        else:
            prod_session.add(Model(
                provider_id=provider_id,
                model_id=model_id,
                **{f: getattr(dev_m, f) for f in fields},
            ))
            stats.added += 1

    # Deprecate prod models not in dev
    now = datetime.now(timezone.utc)
    for model_id, prod_m in prod_models.items():
        if model_id not in dev_models and prod_m.deprecated_at is None:
            prod_m.deprecated_at = now

    return stats


async def _promote_voices(
    provider_id: str,
    dev_session: AsyncSession,
    prod_session: AsyncSession,
) -> SectionStats:
    dev_result = await dev_session.execute(
        select(Voice).where(Voice.provider_id == provider_id)
    )
    dev_voices = {v.voice_id: v for v in dev_result.scalars().all()}

    prod_result = await prod_session.execute(
        select(Voice).where(Voice.provider_id == provider_id)
    )
    prod_voices = {v.voice_id: v for v in prod_result.scalars().all()}

    fields = ["display_name", "gender", "category", "languages", "description",
              "preview_url", "accent", "age", "use_cases", "tags",
              "compatible_models", "meta", "deprecated_at"]
    stats = SectionStats()

    for voice_id, dev_v in dev_voices.items():
        if voice_id in prod_voices:
            for f in fields:
                setattr(prod_voices[voice_id], f, getattr(dev_v, f))
            stats.updated += 1
        else:
            prod_session.add(Voice(
                provider_id=provider_id,
                voice_id=voice_id,
                **{f: getattr(dev_v, f) for f in fields},
            ))
            stats.added += 1

    now = datetime.now(timezone.utc)
    for voice_id, prod_v in prod_voices.items():
        if voice_id not in dev_voices and prod_v.deprecated_at is None:
            prod_v.deprecated_at = now

    return stats


async def promote_provider(
    provider_id: str,
    dev_session: AsyncSession,
    prod_session: AsyncSession,
    apply: bool,
) -> bool:
    print(f"\n{'─' * 52}")
    print(f"  {provider_id}")
    print(f"{'─' * 52}")

    stt_stats = await _promote_models(provider_id, "stt", dev_session, prod_session)
    tts_stats = await _promote_models(provider_id, "tts", dev_session, prod_session)
    voice_stats = await _promote_voices(provider_id, dev_session, prod_session)

    print(f"  STT models : {stt_stats}")
    print(f"  TTS models : {tts_stats}")
    print(f"  TTS voices : {voice_stats}")

    if not any(s.has_changes for s in [stt_stats, tts_stats, voice_stats]):
        print("  No changes — prod already up to date.")
        await prod_session.rollback()
        return True

    if not apply:
        print("  Dry-run — no changes written. Run with --apply to promote to prod.")
        await prod_session.rollback()
        return True

    await _promote_provider(provider_id, dev_session, prod_session)
    await prod_session.commit()
    print("  ✓ Promoted to prod.")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Promote dev DB → prod DB")
    parser.add_argument("providers", nargs="*", help="Provider IDs (default: all)")
    parser.add_argument("--apply", action="store_true", help="Write to prod DB (default: dry-run only)")
    args = parser.parse_args()

    if not settings.prod_database_url:
        print("✗ PROD_DATABASE_URL is not set in .env")
        return

    print(f"Dev DB : {settings.database_url}")
    print(f"Prod DB: {settings.prod_database_url}")

    dev_engine = create_async_engine(settings.database_url)
    prod_engine = create_async_engine(settings.prod_database_url)
    DevSession = sessionmaker(dev_engine, class_=AsyncSession, expire_on_commit=False)
    ProdSession = sessionmaker(prod_engine, class_=AsyncSession, expire_on_commit=False)

    # Discover providers from dev DB if none specified
    if args.providers:
        provider_ids = args.providers
    else:
        async with DevSession() as dev_session:
            result = await dev_session.execute(
                select(Provider).where(Provider.deprecated_at.is_(None))
            )
            provider_ids = [p.provider_id for p in result.scalars().all()]

    if not provider_ids:
        print("No providers found in dev DB. Run sync.py first.")
        return

    print(f"Providers: {', '.join(provider_ids)}")

    success = 0
    for provider_id in provider_ids:
        async with DevSession() as dev_session, ProdSession() as prod_session:
            if await promote_provider(provider_id, dev_session, prod_session, apply=args.apply):
                success += 1

    print(f"\n{'═' * 52}")
    if args.apply:
        print(f"  {success}/{len(provider_ids)} providers promoted to prod")
    else:
        print(f"  Dry-run complete — no changes written.")
        print(f"  Review the diff above, then run with --apply to promote to prod.")

    await dev_engine.dispose()
    await prod_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
