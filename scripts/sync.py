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
import importlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from naaviq.config import settings
from naaviq.models import Model, Provider, Voice
from naaviq.sync.base import SyncModel, SyncResult, SyncVoice

_SYNCERS: dict[str, str] = {
    "deepgram":     "naaviq.sync.deepgram.DeepgramSyncer",
    "cartesia":     "naaviq.sync.cartesia.CartesiaSyncer",
    "elevenlabs":   "naaviq.sync.elevenlabs.ElevenLabsSyncer",
    "openai":       "naaviq.sync.openai.OpenAISyncer",
    "google-cloud": "naaviq.sync.google_cloud.GoogleCloudSyncer",
    "sarvam":       "naaviq.sync.sarvam.SarvamSyncer",
    "azure":        "naaviq.sync.azure.AzureSyncer",
    "amazon-polly": "naaviq.sync.amazon_polly.AmazonPollySyncer",
    "humeai":       "naaviq.sync.humeai.HumeAISyncer",
    "inworld":      "naaviq.sync.inworld.InworldAISyncer",
    "murf":         "naaviq.sync.murf.MurfAISyncer",
    "speechmatics": "naaviq.sync.speechmatics.SpeechmaticsSyncer",
    "lmnt":         "naaviq.sync.lmnt.LmntSyncer",
    "rime":         "naaviq.sync.rime.RimeSyncer",
    "assemblyai":   "naaviq.sync.assemblyai.AssemblyAISyncer",
    "revai":        "naaviq.sync.revai.RevAISyncer",
    "gladia":       "naaviq.sync.gladia.GladiaSyncer",
    "minimax":      "naaviq.sync.minimax.MinimaxSyncer",
    "ibm":          "naaviq.sync.ibm.IBMSyncer",
    "neuphonic":         "naaviq.sync.neuphonic.NeurophonicSyncer",
    "amazon-transcribe": "naaviq.sync.amazon_transcribe.AmazonTranscribeSyncer",
    "resemble":          "naaviq.sync.resemble.ResembleSyncer",
    "fishaudio":         "naaviq.sync.fishaudio.FishAudioSyncer",
    "unrealspeech":      "naaviq.sync.unrealspeech.UnrealSpeechSyncer",
    "smallestai":        "naaviq.sync.smallestai.SmallestAISyncer",
    "lovoai":            "naaviq.sync.lovoai.LovoAISyncer",
    "mistral":           "naaviq.sync.mistral.MistralSyncer",
    "wellsaid":          "naaviq.sync.wellsaid.WellSaidSyncer",
}

_PROVIDER_META: dict[str, dict] = {
    "deepgram":     {"display_name": "Deepgram",      "type": "both"},
    "cartesia":     {"display_name": "Cartesia",      "type": "both"},
    "elevenlabs":   {"display_name": "ElevenLabs",    "type": "both"},
    "openai":       {"display_name": "OpenAI",        "type": "both"},
    "google-cloud": {"display_name": "Google Cloud",  "type": "both"},
    "sarvam":       {"display_name": "Sarvam",        "type": "both"},
    "azure":        {"display_name": "Azure Speech",  "type": "both"},
    "amazon-polly": {"display_name": "Amazon Polly",  "type": "tts"},
    "humeai":       {"display_name": "Hume AI",       "type": "tts"},
    "inworld":      {"display_name": "Inworld AI",    "type": "both"},
    "murf":         {"display_name": "Murf AI",       "type": "tts"},
    "speechmatics": {"display_name": "Speechmatics",  "type": "stt"},
    "lmnt":         {"display_name": "LMNT",          "type": "tts"},
    "rime":         {"display_name": "Rime AI",       "type": "tts"},
    "assemblyai":   {"display_name": "AssemblyAI",    "type": "stt"},
    "revai":        {"display_name": "Rev AI",        "type": "stt"},
    "gladia":       {"display_name": "Gladia",        "type": "stt"},
    "minimax":      {"display_name": "MiniMax",       "type": "tts"},
    "ibm":          {"display_name": "IBM Watson",    "type": "both"},
    "neuphonic":         {"display_name": "Neuphonic",            "type": "tts"},
    "amazon-transcribe": {"display_name": "Amazon Transcribe",    "type": "stt"},
    "resemble":          {"display_name": "Resemble AI",          "type": "tts"},
    "fishaudio":         {"display_name": "Fish Audio",            "type": "both"},
    "unrealspeech":      {"display_name": "Unreal Speech",         "type": "tts"},
    "smallestai":        {"display_name": "Smallest AI",           "type": "both"},
    "lovoai":            {"display_name": "Lovo AI",               "type": "tts"},
    "mistral":           {"display_name": "Mistral AI",            "type": "both"},
    "wellsaid":          {"display_name": "WellSaid Labs",         "type": "tts"},
}

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
        meta = _PROVIDER_META.get(provider_id, {"display_name": provider_id, "type": "both"})
        session.add(Provider(provider_id=provider_id, **meta))
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
    stats = SectionStats()

    for sync_m in incoming:
        payload = {k: v for k, v in vars(sync_m).items()}
        payload["type"] = type_
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


async def sync_provider(provider_id: str, session: AsyncSession, apply: bool) -> bool:
    print(f"\n{'─' * 52}")
    print(f"  {provider_id}")
    print(f"{'─' * 52}")

    syncer_path = _SYNCERS[provider_id]
    module_path, class_name = syncer_path.rsplit(".", 1)
    syncer = getattr(importlib.import_module(module_path), class_name)()

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
        return False
    print(" done")

    now = datetime.now(timezone.utc)
    await _ensure_provider(provider_id, session)

    stt_stats = await _apply_models(provider_id, "stt", result.stt_models, now, session)
    tts_stats = await _apply_models(provider_id, "tts", result.tts_models, now, session)
    voice_stats = await _apply_voices(provider_id, result.tts_voices, now, session)

    print(f"  STT models : {stt_stats}")
    print(f"  TTS models : {tts_stats}")
    print(f"  TTS voices : {voice_stats}")

    if not any(s.has_changes for s in [stt_stats, tts_stats, voice_stats]):
        print("  No changes — already up to date.")
        await session.rollback()
        return True

    if not apply:
        print("  Dry-run — no changes written. Run with --apply to write to dev DB.")
        await session.rollback()
        return True

    result2 = await session.execute(select(Provider).where(Provider.provider_id == provider_id))
    provider = result2.scalar_one_or_none()
    if provider:
        provider.source = result.source
        provider.last_synced_at = now
        if result.api_urls:
            provider.api_urls = result.api_urls
        if result.docs_urls:
            provider.docs_urls = result.docs_urls

    await session.commit()
    print("  ✓ Applied to dev DB.")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync voice providers to dev DB")
    parser.add_argument("providers", nargs="*", help="Provider IDs (default: all)")
    parser.add_argument("--apply", action="store_true", help="Write changes to dev DB (default: dry-run only)")
    args = parser.parse_args()

    if args.providers:
        unknown = [p for p in args.providers if p not in _SYNCERS]
        if unknown:
            print(f"Unknown providers: {', '.join(unknown)}")
            print(f"Available: {', '.join(_SYNCERS)}")
            return

    provider_ids = args.providers or list(_SYNCERS.keys())

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Dev DB : {settings.database_url}")
    print(f"Mode   : {mode}")
    print(f"Syncing: {', '.join(provider_ids)}")

    engine = create_async_engine(settings.database_url)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    success = 0
    for provider_id in provider_ids:
        async with Session() as session:
            if await sync_provider(provider_id, session, apply=args.apply):
                success += 1

    print(f"\n{'═' * 52}")
    if args.apply:
        print(f"  {success}/{len(provider_ids)} providers synced to dev DB")
        if success < len(provider_ids):
            print(f"  {len(provider_ids) - success} failed — check errors above")
        print(f"\n  To promote to prod: uv run python scripts/promote.py")
    else:
        print(f"  Dry-run complete — no changes written.")
        print(f"  Review the diff above, then run with --apply to write to dev DB.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
