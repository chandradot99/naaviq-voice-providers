"""
Sync voice providers to the local (dev) database.

Two-step workflow designed for Claude Code:
  Step 1 — dry-run (default): fetch + show diff, no DB write
  Step 2 — apply: write diff to DB

Usage:
    uv run python scripts/sync.py rime               # dry-run: show diff only
    uv run python scripts/sync.py rime --apply       # apply diff to dev DB
    uv run python scripts/sync.py --apply            # apply all providers
    uv run python scripts/sync.py cartesia deepgram  # dry-run multiple

To promote dev → prod: uv run python scripts/promote.py
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from naaviq.config import settings
from naaviq.models import Model, Provider, SyncRun, Voice, derive_provider_type
from naaviq.sync.base import SyncModel, SyncResult, SyncVoice
from naaviq.sync.cache import _models_path, _voices_path
from naaviq.sync.registry import BY_ID, REGISTRY, load_syncer

_SYNC_ERROR_MAX_CHARS = 8000   # cap stored error text

_MODEL_IDENTITY = {"model_id", "type"}
_VOICE_IDENTITY = {"voice_id"}


@dataclass
class SectionStats:
    added: int = 0
    updated: int = 0
    deprecated: int = 0

    def __str__(self) -> str:
        return f"+{self.added}  ~{self.updated}  -{self.deprecated}"

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.updated or self.deprecated)


async def _ensure_provider(provider_id: str, session: AsyncSession) -> None:
    result = await session.execute(select(Provider).where(Provider.provider_id == provider_id))
    if not result.scalar_one_or_none():
        entry = BY_ID[provider_id]  # main() already validated provider_id is in the registry
        session.add(Provider(
            provider_id=provider_id,
            display_name=entry.display_name,
            type=entry.type,
        ))
        await session.flush()
        print(f"  Created provider record for '{provider_id}'")


async def _apply_models(
    provider_id: str,
    type_: str,
    incoming: list[SyncModel],
    now: datetime,
    session: AsyncSession,
) -> SectionStats:
    result = await session.execute(
        select(Model)
        .where(Model.provider_id == provider_id)
        .where(Model.type == type_)
    )
    existing = {m.model_id: m for m in result.scalars().all()}
    incoming_ids = {m.model_id for m in incoming}
    incoming_by_id = {m.model_id: m for m in incoming}
    stats = SectionStats()

    # Demote existing defaults whose incoming version is no longer the default,
    # and flush before promoting — the partial unique index
    # `uq_models_one_default_per_provider_type` is checked per-row at UPDATE time,
    # so a promote-before-demote order can fail with a unique violation.
    demoted = False
    for model_id, existing_m in existing.items():
        if not existing_m.is_default or existing_m.deprecated_at is not None:
            continue
        incoming_m = incoming_by_id.get(model_id)
        if incoming_m is None or not incoming_m.is_default:
            existing_m.is_default = False
            demoted = True
    if demoted:
        await session.flush()

    for sync_m in incoming:
        payload = {k: v for k, v in vars(sync_m).items()}
        payload["type"] = type_
        # SyncModel.eol_date is "YYYY-MM-DD" string (ai_parser/cache emit strings);
        # DB column is Date. asyncpg doesn't always coerce — do it explicitly here.
        if isinstance(payload.get("eol_date"), str):
            payload["eol_date"] = date.fromisoformat(payload["eol_date"])
        if sync_m.model_id in existing:
            m = existing[sync_m.model_id]
            for field, value in payload.items():
                if field not in _MODEL_IDENTITY:
                    setattr(m, field, value)
            m.deprecated_at = None
            stats.updated += 1
        else:
            session.add(Model(provider_id=provider_id, **payload))
            stats.added += 1

    for model_id, m in existing.items():
        if model_id not in incoming_ids and m.deprecated_at is None:
            m.deprecated_at = now
            m.lifecycle = "deprecated"   # DB CHECK: deprecated_at and lifecycle='deprecated' are in lockstep
            stats.deprecated += 1

    return stats


async def _apply_voices(
    provider_id: str,
    incoming: list[SyncVoice],
    now: datetime,
    session: AsyncSession,
) -> SectionStats:
    result = await session.execute(
        select(Voice).where(Voice.provider_id == provider_id)
    )
    existing = {v.voice_id: v for v in result.scalars().all()}
    incoming_ids = {v.voice_id for v in incoming}
    stats = SectionStats()

    for sync_v in incoming:
        payload = {k: v for k, v in vars(sync_v).items()}
        if sync_v.voice_id in existing:
            v = existing[sync_v.voice_id]
            for field, value in payload.items():
                if field not in _VOICE_IDENTITY:
                    setattr(v, field, value)
            v.deprecated_at = None
            stats.updated += 1
        else:
            session.add(Voice(provider_id=provider_id, **payload))
            stats.added += 1

    for voice_id, v in existing.items():
        if voice_id not in incoming_ids and v.deprecated_at is None:
            v.deprecated_at = now
            stats.deprecated += 1

    return stats


def _section_stats_dict(s: SectionStats) -> dict:
    return {"added": s.added, "updated": s.updated, "deprecated": s.deprecated}


def _clear_provider_cache(provider_id: str) -> None:
    """Remove .sync-cache/ files for this provider so the next sync is forced to re-parse.

    Run only after an --apply. Dry-runs preserve cache so the operator can apply
    without re-parsing.
    """
    for p in (
        _models_path(provider_id, "stt"),
        _models_path(provider_id, "tts"),
        _voices_path(provider_id),
    ):
        p.unlink(missing_ok=True)


async def _record_sync_error(
    provider_id: str,
    started_at: datetime,
    error_msg: str,
    session: AsyncSession,
    source: str | None = None,
) -> None:
    """Rollback pending changes, re-ensure the provider, write an error sync_run, commit.

    `source` is None for fetch-stage errors (we never got a SyncResult), or the
    result's source for apply-stage errors.
    """
    await session.rollback()
    await _ensure_provider(provider_id, session)
    session.add(SyncRun(
        provider_id=provider_id,
        status="error",
        source=source,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        stats={},
        error=error_msg[:_SYNC_ERROR_MAX_CHARS],
    ))
    await session.commit()


async def sync_provider(provider_id: str, session: AsyncSession, apply: bool) -> bool:
    print(f"\n{'─' * 52}")
    print(f"  {provider_id}")
    print(f"{'─' * 52}")

    started_at = datetime.now(timezone.utc)
    syncer = load_syncer(provider_id)

    print("  Fetching...", end="", flush=True)
    try:
        result: SyncResult = await syncer.sync()
    except Exception as e:
        msg = str(e)
        if "no cache found" in msg or "ANTHROPIC_API_KEY not set" in msg:
            print(f"\n  ⚠  Needs AI extraction — {msg.splitlines()[0]}")
            for line in msg.splitlines()[1:]:
                print(f"     {line.strip()}")
        else:
            print(f"\n  ✗ {e}")
        if apply:
            await _record_sync_error(provider_id, started_at, f"{type(e).__name__}: {e}", session)
        else:
            await session.rollback()
        return False
    print(" done")

    now = datetime.now(timezone.utc)
    try:
        await _ensure_provider(provider_id, session)

        stt_stats = await _apply_models(provider_id, "stt", result.stt_models, now, session)
        tts_stats = await _apply_models(provider_id, "tts", result.tts_models, now, session)
        voice_stats = await _apply_voices(provider_id, result.tts_voices, now, session)

        print(f"  STT models : {stt_stats}")
        print(f"  TTS models : {tts_stats}")
        print(f"  TTS voices : {voice_stats}")

        if not apply:
            if not any(s.has_changes for s in [stt_stats, tts_stats, voice_stats]):
                print("  No changes — already up to date.")
            else:
                print("  Dry-run — no changes written. Run with --apply to write to dev DB.")
            await session.rollback()
            return True

        provider = (await session.execute(
            select(Provider).where(Provider.provider_id == provider_id)
        )).scalar_one()
        provider.source = result.source
        provider.last_synced_at = now
        if result.api_urls:
            provider.api_urls = result.api_urls
        if result.docs_urls:
            provider.docs_urls = result.docs_urls
        derived_type = await derive_provider_type(provider_id, session)
        if derived_type and provider.type != derived_type:
            provider.type = derived_type

        session.add(SyncRun(
            provider_id=provider_id,
            status="success",
            source=result.source,
            started_at=started_at,
            finished_at=now,
            stats={
                "stt":    _section_stats_dict(stt_stats),
                "tts":    _section_stats_dict(tts_stats),
                "voices": _section_stats_dict(voice_stats),
            },
            notes=result.notes,
        ))

        await session.commit()
    except Exception as e:
        print(f"\n  ✗ Apply error: {type(e).__name__}: {e}")
        if apply:
            await _record_sync_error(
                provider_id, started_at, f"{type(e).__name__}: {e}", session,
                source=result.source,
            )
        else:
            await session.rollback()
        return False

    if not any(s.has_changes for s in [stt_stats, tts_stats, voice_stats]):
        print("  ✓ Synced — no changes.")
    else:
        print("  ✓ Applied to dev DB.")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync voice providers to dev DB")
    parser.add_argument("providers", nargs="*", help="Provider IDs (default: all)")
    parser.add_argument("--apply", action="store_true", help="Write changes to dev DB (default: dry-run only)")
    args = parser.parse_args()

    if args.providers:
        unknown = [p for p in args.providers if p not in BY_ID]
        if unknown:
            print(f"Unknown providers: {', '.join(unknown)}")
            print(f"Available: {', '.join(e.provider_id for e in REGISTRY)}")
            return

    provider_ids = args.providers or [e.provider_id for e in REGISTRY]

    if not settings.dev_database_url:
        print("✗ DEV_DATABASE_URL is not set in .env")
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Dev DB : {settings.dev_database_url}")
    print(f"Mode   : {mode}")
    print(f"Syncing: {', '.join(provider_ids)}")

    engine = create_async_engine(settings.dev_database_url)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    success = 0
    for provider_id in provider_ids:
        async with Session() as session:
            ok = await sync_provider(provider_id, session, apply=args.apply)
            if ok:
                success += 1
        if args.apply:
            _clear_provider_cache(provider_id)

    print(f"\n{'═' * 52}")
    if args.apply:
        print(f"  {success}/{len(provider_ids)} providers synced to dev DB")
        if success < len(provider_ids):
            print(f"  {len(provider_ids) - success} failed — check errors above")
        print("\n  To promote to prod: uv run python scripts/promote.py")
    else:
        print("  Dry-run complete — no changes written.")
        print("  Review the diff above, then run with --apply to write to dev DB.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
