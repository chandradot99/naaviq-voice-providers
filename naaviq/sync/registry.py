"""
Single source of truth for the list of provider syncers.

Both `scripts/sync.py` and `naaviq-admin` consume this registry — any new
provider only needs to be registered here once.

Syncer classes are referenced by dotted path (module.Class) and loaded lazily
so importing this module stays cheap and doesn't pull in optional deps
(e.g., anthropic, provider SDKs) until a specific syncer is actually used.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Literal

from naaviq.sync.base import ProviderSyncer


@dataclass(frozen=True)
class SyncerEntry:
    provider_id: str
    display_name: str
    type: Literal["stt", "tts", "both"]
    syncer_path: str   # "naaviq.sync.deepgram.DeepgramSyncer"


REGISTRY: list[SyncerEntry] = [
    SyncerEntry("deepgram",          "Deepgram",          "both", "naaviq.sync.deepgram.DeepgramSyncer"),
    SyncerEntry("cartesia",          "Cartesia",          "both", "naaviq.sync.cartesia.CartesiaSyncer"),
    SyncerEntry("elevenlabs",        "ElevenLabs",        "both", "naaviq.sync.elevenlabs.ElevenLabsSyncer"),
    SyncerEntry("openai",            "OpenAI",            "both", "naaviq.sync.openai.OpenAISyncer"),
    SyncerEntry("google-cloud",      "Google Cloud",      "both", "naaviq.sync.google_cloud.GoogleCloudSyncer"),
    SyncerEntry("sarvam",            "Sarvam",            "both", "naaviq.sync.sarvam.SarvamSyncer"),
    SyncerEntry("azure",             "Azure Speech",      "both", "naaviq.sync.azure.AzureSyncer"),
    SyncerEntry("amazon-polly",      "Amazon Polly",      "tts",  "naaviq.sync.amazon_polly.AmazonPollySyncer"),
    SyncerEntry("humeai",            "Hume AI",           "tts",  "naaviq.sync.humeai.HumeAISyncer"),
    SyncerEntry("inworld",           "Inworld AI",        "both", "naaviq.sync.inworld.InworldAISyncer"),
    SyncerEntry("murf",              "Murf AI",           "tts",  "naaviq.sync.murf.MurfAISyncer"),
    SyncerEntry("speechmatics",      "Speechmatics",      "stt",  "naaviq.sync.speechmatics.SpeechmaticsSyncer"),
    SyncerEntry("lmnt",              "LMNT",              "tts",  "naaviq.sync.lmnt.LmntSyncer"),
    SyncerEntry("rime",              "Rime AI",           "tts",  "naaviq.sync.rime.RimeSyncer"),
    SyncerEntry("assemblyai",        "AssemblyAI",        "stt",  "naaviq.sync.assemblyai.AssemblyAISyncer"),
    SyncerEntry("revai",             "Rev AI",            "stt",  "naaviq.sync.revai.RevAISyncer"),
    SyncerEntry("gladia",            "Gladia",            "stt",  "naaviq.sync.gladia.GladiaSyncer"),
    SyncerEntry("minimax",           "MiniMax",           "tts",  "naaviq.sync.minimax.MinimaxSyncer"),
    SyncerEntry("ibm",               "IBM Watson",        "both", "naaviq.sync.ibm.IBMSyncer"),
    SyncerEntry("neuphonic",         "Neuphonic",         "tts",  "naaviq.sync.neuphonic.NeurophonicSyncer"),
    SyncerEntry("amazon-transcribe", "Amazon Transcribe", "stt",  "naaviq.sync.amazon_transcribe.AmazonTranscribeSyncer"),
    SyncerEntry("resemble",          "Resemble AI",       "tts",  "naaviq.sync.resemble.ResembleSyncer"),
    SyncerEntry("fishaudio",         "Fish Audio",        "both", "naaviq.sync.fishaudio.FishAudioSyncer"),
    SyncerEntry("unrealspeech",      "Unreal Speech",     "tts",  "naaviq.sync.unrealspeech.UnrealSpeechSyncer"),
    SyncerEntry("smallestai",        "Smallest AI",       "both", "naaviq.sync.smallestai.SmallestAISyncer"),
    SyncerEntry("lovoai",            "Lovo AI",           "tts",  "naaviq.sync.lovoai.LovoAISyncer"),
    SyncerEntry("mistral",           "Mistral AI",        "both", "naaviq.sync.mistral.MistralSyncer"),
    SyncerEntry("wellsaid",          "WellSaid Labs",     "tts",  "naaviq.sync.wellsaid.WellSaidSyncer"),
    SyncerEntry("cambai",            "CAMB.ai",           "both", "naaviq.sync.cambai.CambAISyncer"),
    SyncerEntry("speechify",         "Speechify",         "tts",  "naaviq.sync.speechify.SpeechifySyncer"),
    SyncerEntry("typecastai",        "Typecast AI",       "tts",  "naaviq.sync.typecastai.TypecastAISyncer"),
    SyncerEntry("groq",              "Groq",              "both", "naaviq.sync.groq.GroqSyncer"),
]

BY_ID: dict[str, SyncerEntry] = {e.provider_id: e for e in REGISTRY}


def get_syncer_entry(provider_id: str) -> SyncerEntry | None:
    return BY_ID.get(provider_id)


def load_syncer(provider_id: str) -> ProviderSyncer:
    """Import and instantiate the syncer for `provider_id`. Raises KeyError if unknown."""
    entry = BY_ID[provider_id]
    module_path, class_name = entry.syncer_path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_path), class_name)
    return cls()
