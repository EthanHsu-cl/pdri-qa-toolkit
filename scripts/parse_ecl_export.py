#!/usr/bin/env python3
"""PDR-I ECL Export Parser v2.3

Changes from v2.2:

  - Issue 2:
      * Adds `status_active` boolean column.
      * NAB, Won't Fix, Not Reproducible, New Feature, etc. are still INCLUDED
        but flagged as inactive. Nothing is dropped; risk code can downweight or filter.

  - Issue 5:
      * Fuzzy matching (rapidfuzz) for module normalization.
      * Per-version mapping files under data/module_mappings/:
          permanent/mappings_global.json
          versions/<version>_pending.json
          versions/<version>_confirmed.json
        New / low-confidence strings go into <ver>_pending.json with suggested match.

  - Issue 8:
      * Adds `priority_num` and human-readable `priority_label` columns:
          1 = Fix Now, 2 = Must Fix, 3 = Better Fix, 4 = No Matter, 5 = N/A.

Usage:
  python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv \
    --mapping-dir data/module_mappings --fuzzy-threshold 85
"""
import re
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

from typing import Optional

try:
    from rapidfuzz import process, fuzz

    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    print("WARNING: rapidfuzz not installed. Run: pip install rapidfuzz")
    print("         Falling back to exact alias matching only.")

# ---------------------------------------------------------------------
# Issue 2 – Status classification
# ---------------------------------------------------------------------

INACTIVE_STATUSES = {
    "close",
    "need more info",
    "nab",
    "propose nab",
    "wont fix",
    "won't fix",
    "propose wont fix",
    "qa propose wont fix",
    "not reproducible",
    "notreproducible",
    "not a bug",
    "new feature",
    "external issue",
    "hqqa close",
    "fae close",
}


def classify_status(status_val) -> bool:
    """True if active/open, False if resolved/invalidated."""
    if pd.isna(status_val):
        return True
    s = str(status_val).strip().lower()
    return s not in INACTIVE_STATUSES


# ---------------------------------------------------------------------
# Aliases / Categories (truncated to what's needed for behaviour change)
# ---------------------------------------------------------------------

MODULE_ALIASES = {
    "HQ Auido Denoise": "HQ Audio Denoise",
    "Boay Effects": "Body Effects",
    "Speech Enhacner": "Speech Enhancer",
    "AI Anime Vide": "AI Anime Video",
    "AI Anime Vidoeo": "AI Anime Video",
    "AI Voice Change": "AI Voice Changer",
    "Auto Caption": "Auto Captions",
    "Facial Mosiac": "Facial Mosaic",
    "Voice-over": "Voice-Over",
    "voice-Over": "Voice-Over",
    "Voiceover": "Voice-Over",
    "Voice Over": "Voice-Over",
    "Text To Video": "Text to Video",
    "Text To Image": "Text to Image",
    "Image To Video": "Image to Video",
    "text to video": "Text to Video",
    "text to image": "Text to Image",
    "image to video": "Image to Video",
    "Ai Storytelling": "AI Storytelling",
    "AI storytelling": "AI Storytelling",
    "AI Storyteller": "AI Storytelling",
    "AI Storytelljng": "AI Storytelling",
    "Ai Art": "AI Art",
    "AI art": "AI Art",
    "auto edit": "Auto Edit",
    "Auto edit": "Auto Edit",
    "export": "Export",
    "Colour Board": "Color Board",
    "Crop&Rotate": "Crop & Rotate",
    "Crop(Rotate)": "Crop & Rotate",
    "Pan&Zoom": "Pan & Zoom",
    "Beat Marker": "Beats Marker",
    "Beats Maker": "Beats Marker",
    "Key Frame": "Transform Keyframe",
    "Talkling Avatar": "Talking Avatar",
    "TTS": "Text to Speech",
    "slpash": "Splash",
    "Shortuct": "Shortcut",
    "ShortCut": "Shortcut",
    "Skin Smoothness": "Skin Smoother",
    "Face smoother": "Skin Smoother",
    "Face Smoother": "Skin Smoother",
    "Msak": "Mask",
    "Transtion": "Transition",
    "Teansition": "Transition",
    "Clooud Storage": "Cloud Storage",
    "Cloud Stroage": "Cloud Storage",
    "sign in your account": "Sign In",
    "Sing in": "Sign In",
    "ripple editing": "Ripple Editing",
    "Add media": "Add Media",
    "MixPanel": "Mixpanel",
    "Mixpanel log": "Mixpanel",
    "Body Reshape": "Body Effects",
    "AI body Effect": "Body Effects",
    "AI Upscaler": "AI Video Upscaler",
    "AI FX": "AI Effect",
    "Layer Effect": "Effect Layer",
    "Effect layer": "Effect Layer",
    "FX Layer": "Effect Layer",
    "Fx Layer": "Effect Layer",
    "AI Cartoon (Cine Anime)": "AI Cartoon",
    "AI Cartoon (Crop)": "AI Cartoon",
    "Crop range panel": "Crop & Rotate",
}

MODULE_ALIASES.update(
    {
        "AI Video Enahncer": "Video Enhancer",
        "AI Video Enhacner": "Video Enhancer",
        "AI Video Upsacler": "AI Video Upscaler",
        "Video Enhacer(Compare)": "Video Enhancer",
        "Imageo to Video(Cash flow)": "Image to Video",
        "APNG Text": "Text",
        "APNG(style)": "Text",
        "Text(APNG)": "Text",
        "Title, Text, APNG": "Title, Text",
        "AI Music Creation": "AI Music Generator",
        "AI Music Generate": "AI Music Generator",
        "AI Music Genrator": "AI Music Generator",
        "AI Music Gnerator": "AI Music Generator",
        "AI Storytelljng(ENG cheerful Mia)": "AI Storytelling",
        "Shortcut(AI Anime Vidoeo)": "Shortcut(AI Anime Video)",
        "Shortcut(AI Video Anime)": "Shortcut(AI Anime Video)",
        "Clooud Storage(Upload)": "Cloud Storage(Upload)",
        "Cloud Stroage(Previe)": "Cloud Storage(Preview)",
        "Openging Intro": "Open intro",
        "New Open intro, Dark or Light mode": "Opening Intro",
        "Tex, Title": "Text, Title",
        "Text (Colorful": "Text (Colorful)",
        "Text (Gold": "Text (Gold_11~18)",
        "- MGT(Sparkle)": "MGT(Sparkle)",
    }
)

# Lowercase shadow for O(1) case-insensitive alias lookup (built once at import)
_MODULE_ALIASES_LOWER = {k.lower(): v for k, v in MODULE_ALIASES.items()}

MODULE_CATEGORIES = {
    "AI Features": [
        "AI Storytelling",
        "AI Art",
        "AI Smart Tools",
        "Image to Video",
        "Character Motion Swap",
        "Text to Video",
        "AI Anime Video",
        "AI Magic",
        "Talking Avatar",
        "AI Images",
        "Text to Image",
        "AI Style Transfer",
        "AI Sketch",
        "AI Cartoon",
        "AI Scene",
        "AI Effect",
        "AI Creation",
        "GAI",
        "AI Scene/Art",
        "Voice Cloning",
        "My Voices",
        "TTI Inspiration",
    ],
    "Editor Core": [
        "Project",
        "Auto Edit",
        "Auto Edit Project",
        "Recent Projects",
        "New Project",
        "Split",
        "Duplicate",
        "Trim",
        "Adjust Speed",
        "Speed",
        "Standard Speed",
        "Speed Curve",
        "Super Slow Motion",
        "Transform",
        "Crop & Rotate",
        "Pan & Zoom",
        "Fill",
        "Fit",
        "Flip",
        "Transform Keyframe",
        "Reduce Track Height",
        "Aspect Ratio",
        "Reverse",
        "Freeze Frame",
        "Range",
        "Replace",
        "Timeline",
        "Editor",
        "Edit Menu",
        "NewTimeline",
        "Ripple Editing",
        "PIP",
        "PiP",
        "Drag and Drop",
    ],
    "Audio": [
        "Audio",
        "Music",
        "AI Music Generator",
        "Voice-Over",
        "Sound FX",
        "Extract Audio",
        "Text to Speech",
        "HQ Audio Denoise",
        "Denoise",
        "Speech Enhancer",
        "AI Voice Changer",
        "Audio Ducking",
        "Audio Mixing",
        "Beats Marker",
        "Volume",
        "Audio Tool",
        "Meta Sound",
        "My Voice",
    ],
    "Text & Captions": [
        "Text",
        "Add Text",
        "Auto Captions",
        "Edit Text",
        "Captions List",
        "Bilingual Subtitles",
        "Animation",
        "Template",
        "Style",
        "Font",
        "Backdrop",
        "MGT",
        "Title",
        "Auto Caption",
        "Intro/Outro",
    ],
    "Visual Effects": [
        "Effects",
        "Filter",
        "Video Effects",
        "Effect Overlay",
        "Body Effects",
        "Highlight",
        "Sticker",
        "Motion",
        "Blending",
        "Opacity",
        "Border & Shadow",
        "Mosaic & Blur",
        "Effect Layer",
        "AI Effect",
        "Magnifier",
    ],
    "Color & Adjust": [
        "Adjust",
        "AI Color",
        "Brightness",
        "Contrast",
        "Saturation",
        "HSL",
        "Hue",
        "Temp",
        "Tint",
        "Sharpness",
        "Color Board",
        "Color Selector",
        "HDR",
    ],
    "Background & Cutout": [
        "Background",
        "Add Background",
        "Cutout",
        "Replace Background",
        "Replace BG",
        "Chroma Key",
        "Mask",
    ],
    "Enhance & Fix": [
        "Enhance",
        "Video Enhancer",
        "Photo Enhance",
        "Video Enhance",
        "Fix Shake",
        "AI Video Upscaler",
        "Video Upscaler",
        "Facial Mosaic",
        "Auto Mosaic",
        "Skin Smoother",
        "Portrait",
        "People Background",
        "Motion Tracking",
        "Face Reshape",
        "Body Reshape",
        "Stabilizer",
    ],
    "Export & Output": [
        "Export",
        "Produced Video",
        "Video Intro",
        "Video Outro",
        "Share Page",
        "Share extension",
        "Produce",
        "Preview",
    ],
    "UI & Settings": [
        "Settings",
        "Preference",
        "Tutorials & Tips",
        "VIP Benefit page",
        "Trending",
        "Camera",
        "Video",
        "Photo",
        "Overlay",
        "Transition",
        "Templates",
        "Duration",
        "Credit",
        "More",
        "My Artwork",
        "Feedback",
        "Send Feedback",
        "What's New",
        "Permission",
        "GDPR",
        "Privacy policy",
        "About",
        "Notice",
        "Home indicator",
        "App icon",
    ],
    "Launcher": ["Launcher", "Launch", "Opening Intro", "Splash", "Opening Tutorial", "Recent Task"],
    "Media Picker": [
        "Add Media",
        "Media Picker",
        "Media Room",
        "File Picker",
        "Library",
        "Gettyimages",
        "Pexels",
        "iStock",
        "Cloud Storage (Media Picker)",
    ],
    "Cloud": ["Cloud Storage", "My Cloud", "Back Up to My Device"],
    "IAP": [
        "IAP",
        "Pro+",
        "Subscribe",
        "Upgrade Dialog",
        "Anniversary Sale",
        "Xmas",
        "Halloween",
        "Halloween IAP",
        "JP Golden Week",
        "Summer Sale",
        "New Year Sale",
        "Christmas",
        "Churn Recovery",
    ],
    "Analytics": ["Mixpanel", "Appsflyer", "Flurry Log"],
    "Sign In": ["Sign In", "Social Sign In", "Account", "User Sign In", "GDPR"],
    "Shortcut": ["Shortcut"],
    "Notification": ["Notification", "Push Notification", "Menu(Notification)"],
    "QA / Testing": ["Demo", "Setup", "Installer Package", "Third party", "CS", "App icon"],
}

_FLAT_OVERRIDES = {
    # Enhance & Fix overrides
    "Body Effects": "Enhance & Fix",
    "Body Effect": "Enhance & Fix",
    "AI body Effect": "Enhance & Fix",
    "Body Reshape": "Enhance & Fix",
    "Body Effects (Slim)": "Enhance & Fix",
    "Body Effects(Slim)": "Enhance & Fix",
    # Analytics
    "Mixpanel": "Analytics",
    "MixPanel": "Analytics",
    "Mixpanel log": "Analytics",
    "Appsflyer": "Analytics",
    "Flurry Log": "Analytics",
    # IAP
    "IAP": "IAP",
    "IAP (7-day Free Trial)": "IAP",
    "IAP(7-day Free Trial)": "IAP",
    "IAP(B version)": "IAP",
    "IAP(C version)": "IAP",
    "IAP(A version)": "IAP",
    "IAP(Pro+)": "IAP",
    "IAP(Anniversary)": "IAP",
    "IAP(Xmas)": "IAP",
    "IAP(JP Golden Week)": "IAP",
    "Xmas": "IAP",
    "Xmas/New Year": "IAP",
    "Halloween": "IAP",
    "Halloween IAP": "IAP",
    "JP Golden Week": "IAP",
    "New Year Sale (IAP)": "IAP",
    "Anniversary Sale(IAP)": "IAP",
    "Churn Recovery": "IAP",
    "Cross Promote": "IAP",
    "Cross-Promote": "IAP",
    "Pro+": "IAP",
    "Subscribe": "IAP",
    # Launcher
    "Launcher": "Launcher",
    "Launch": "Launcher",
    "Opening Intro": "Launcher",
    "Opening intro": "Launcher",
    "Opening Tutorial": "Launcher",
    "Splash": "Launcher",
    "Recent Task": "Launcher",
    "Recent task": "Launcher",
    # Cloud
    "Cloud Storage": "Cloud",
    "My Cloud": "Cloud",
    "Back Up to My Device": "Cloud",
    "Cloud": "Cloud",
    # AI Features subset
    "AI Sketch": "AI Features",
    "AI Cartoon": "AI Features",
    "AI Scene": "AI Features",
    "AI Effect": "AI Features",
    "AI Creation": "AI Features",
    "GAI": "AI Features",
    "Voice Cloning": "AI Features",
    "My Voices": "AI Features",
    "TTI Inspiration": "AI Features",
    # Media Picker
    "Add Media": "Media Picker",
    "Media Picker": "Media Picker",
    # Editor core
    "Timeline": "Editor Core",
    "NewTimeline": "Editor Core",
    "Editor": "Editor Core",
    "PiP": "Editor Core",
    "PIP": "Editor Core",
    "Drag and Drop": "Editor Core",
    "Ripple Editing": "Editor Core",
    # Text
    "MGT": "Text & Captions",
    "Intro/Outro": "Text & Captions",
    # Export & Output
    "Share Page": "Export & Output",
    "Share page": "Export & Output",
    "Preview": "Export & Output",
    "Full Screen Preview": "Export & Output",
    # Visual Effects
    "Effect Layer": "Visual Effects",
    "Effect layer": "Visual Effects",
    # Enhance & Fix
    "Face Reshape": "Enhance & Fix",
    "Skin Smoother": "Enhance & Fix",
    "Stabilizer": "Enhance & Fix",
    # Sign In
    "Sign In": "Sign In",
    "Sign in": "Sign In",
    "Social Sign In": "Sign In",
    "User Sign In": "Sign In",
    "Account": "Sign In",
    # Shortcut
    "Shortcut": "Shortcut",
    # Notification
    "Notification": "Notification",
    "Push Notification": "Notification",
    # UI & Settings
    "Settings": "UI & Settings",
    "Menu": "UI & Settings",
    "What's New": "UI & Settings",
    "Watermark": "UI & Settings",
    "Feedback": "UI & Settings",
    "Permission": "UI & Settings",
    "GDPR": "UI & Settings",
    "About": "UI & Settings",
    # QA / Testing
    "Demo": "QA / Testing",
    "Setup": "QA / Testing",
    "CS": "QA / Testing",
    "Third party": "QA / Testing",
}

MODULE_CATEGORIES.update(_FLAT_OVERRIDES)

_MODULE_TO_CATEGORY = {}
for cat, modules in MODULE_CATEGORIES.items():
    if isinstance(modules, list):
        for m in modules:
            _MODULE_TO_CATEGORY[m.lower()] = cat
    elif isinstance(modules, str):
        _MODULE_TO_CATEGORY[cat.lower()] = modules

_CANONICAL_MODULES = list(
    {
        m
        for mods in MODULE_CATEGORIES.values()
        if isinstance(mods, list)
        for m in mods
    }
)

KNOWN_TAGS = [
    "EDF",
    "UX",
    "MX",
    "MUI",
    "Side Effect",
    "AT Found",
    "Function",
    "Stability",
    "Performance",
    "Regression",
    "Crash",
    "ANR",
    "UI",
    "Localization",
    "IAP",
]

SEVERITY_MAP = {
    "1": (1, 10, "1-Critical"),
    "2": (2, 5, "2-Major"),
    "3": (3, 2, "3-Normal"),
    "4": (4, 1, "4-Minor"),
}

# Issue 8 – RD priority labels
PRIORITY_LABEL_MAP = {
    1: "Fix Now",
    2: "Must Fix",
    3: "Better Fix",
    4: "No Matter",
    5: "N/A",
}

DESC_PATTERN = re.compile(
    r"^(?P<product>[A-Z0-9-]+)\s+"
    r"(?P<version>[\d.]+(?:\s*\([\d.]+\))?)\s*"
    r"-\s*"
    r"(?P<tags>(?:\[[^\]]*\]\s*)*)"
    r"(?P<module>[^:]+?):\s*"
    r"(?P<description>.+)$",
    re.IGNORECASE,
)
TAG_PATTERN = re.compile(r"\[([^\]]+)\]")


# ---------------------------------------------------------------------
# Version Mapping Store (Issue 5)
# ---------------------------------------------------------------------


class VersionMappingStore:
    """Manages per-version pending/confirmed module mappings."""

    def __init__(self, mapping_dir: Path):
        self.mapping_dir = mapping_dir
        self.permanent_path = mapping_dir / "permanent" / "mappings_global.json"
        self.versions_dir = mapping_dir / "versions"

        self.mapping_dir.mkdir(parents=True, exist_ok=True)
        self.permanent_path.parent.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)

        self._permanent = self._load_json(self.permanent_path)
        self._confirmed_cache: dict[str, dict] = {}

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_json(self, path: Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_confirmed(self, version: str) -> dict:
        if version not in self._confirmed_cache:
            p = self.versions_dir / f"{version}_confirmed.json"
            self._confirmed_cache[version] = self._load_json(p)
        return self._confirmed_cache[version]

    def lookup(self, raw: str, version: str) -> Optional[str]:
        """Lookup order: permanent -> all confirmed versions.
        Confirmed files are loaded once and cached — avoids repeated disk scans
        across 10k+ bug rows.
        """
        if raw in self._permanent:
            return self._permanent[raw]

        # Build merged confirmed cache once; invalidated only when a new
        # confirmed entry is written (see _save_json path in normalize_module)
        if not hasattr(self, "_all_confirmed_cache"):
            self._all_confirmed_cache = {}
            for p in sorted(self.versions_dir.glob("*_confirmed.json")):
                self._all_confirmed_cache.update(self._load_json(p))
        if raw in self._all_confirmed_cache:
            return self._all_confirmed_cache[raw]
        return None

    def _invalidate_confirmed_cache(self):
        """Call after writing any confirmed file to force a cache rebuild."""
        if hasattr(self, "_all_confirmed_cache"):
            del self._all_confirmed_cache

    def add_pending(self, raw: str, suggested: str, version: str):
        p_path = self.versions_dir / f"{version}_pending.json"
        pending = self._load_json(p_path)
        if raw not in pending:
            pending[raw] = {"suggested": suggested, "confirmed": False}
            self._save_json(p_path, pending)

    def flush_pending(self, version: str) -> dict:
        p_path = self.versions_dir / f"{version}_pending.json"
        pending = self._load_json(p_path)
        return {k: v for k, v in pending.items() if not v.get("confirmed", False)}

    def promote_to_permanent(self, version: str):
        confirmed = self._get_confirmed(version)
        self._permanent.update(confirmed)
        self._save_json(self.permanent_path, self._permanent)
        print(
            f"  ✅ Promoted {len(confirmed)} mappings from {version} to permanent store."
        )


def normalize_module(raw: str, version: str = "unknown",
                     store: VersionMappingStore = None,
                     fuzzy_threshold: int = 85) -> str:
    """Normalize raw module string -> canonical.

    1. Strip parenthetical sub-variants e.g. "Auto Edit(Pet 02)" -> "Auto Edit"
    2. Exact alias
    3. Mapping store (permanent + confirmed versions)
    4. Fuzzy match (>= threshold -> auto-confirm, 65-threshold -> pending)
    5. Raw string (Uncategorized; flagged for dashboard)
    """
    mod = raw.strip()

    # Step 1 - strip sub-variant parentheticals for grouping/matching purposes.
    # The raw value (e.g. "Auto Edit(Pet 02)") is preserved in parsed_module_raw.
    # The stripped value (e.g. "Auto Edit") is used for all downstream aggregation.
    # Only strips if something remains, so "(unknown)" style names are left intact.
    stripped = re.sub(r'\s*\([^)]*\)', '', mod).strip()
    if stripped:
        mod = stripped

    # Aliases: single O(1) dict lookup (case-insensitive via lowercase shadow built at import time)
    alias_result = _MODULE_ALIASES_LOWER.get(mod.lower())
    if alias_result:
        return alias_result

    # Mapping store
    if store is not None:
        result = store.lookup(mod, version)
        if result:
            return result

    # Fuzzy match
    if FUZZY_AVAILABLE and len(mod) >= 4:
        match, score, _ = process.extractOne(
            mod, _CANONICAL_MODULES, scorer=fuzz.token_sort_ratio
        )
        if score >= fuzzy_threshold:
            if store is not None:
                confirmed_path = store.versions_dir / f"{version}_confirmed.json"
                confirmed = store._load_json(confirmed_path)
                if mod not in confirmed:
                    confirmed[mod] = match
                    store._save_json(confirmed_path, confirmed)
                    store._confirmed_cache[version] = confirmed
                    store._invalidate_confirmed_cache()
            return match
        elif score >= 65 and store is not None:
            store.add_pending(mod, match, version)

    return mod


def get_category(module_name: str) -> str:
    cat = _FLAT_OVERRIDES.get(module_name)
    if cat:
        return cat
    cat = _MODULE_TO_CATEGORY.get(module_name.lower())
    if cat:
        return cat

    mn = module_name.lower()
    for cat_name, modules in MODULE_CATEGORIES.items():
        if isinstance(modules, list):
            for m in modules:
                if len(m) >= 6 and (m.lower() in mn or mn in m.lower()):
                    return cat_name
    return "Uncategorized"


def parse_severity(sev_str):
    if pd.isna(sev_str):
        return 3, 2, "3-Normal"
    d = re.search(r"(\d)", str(sev_str).strip())
    if d:
        return SEVERITY_MAP.get(d.group(1), (3, 2, "3-Normal"))
    return 3, 2, "3-Normal"


def parse_repro(repro_str) -> float:
    if pd.isna(repro_str):
        return 0.5
    s = str(repro_str).strip().lower()
    m = re.search(r"(\d+)\s*(?:out\s*of|/)(\d+)", s)
    if m:
        return int(m.group(1)) / max(int(m.group(2)), 1)
    if "always" in s or "every" in s:
        return 1.0
    if "random" in s:
        return 0.3
    if "once" in s or "rare" in s:
        return 0.1
    return 0.5


def parse_ecl_export(
    input_path: str,
    output_path: str,
    mapping_dir: str = "data/module_mappings",
    fuzzy_threshold: int = 85,
):
    ext = Path(input_path).suffix.lower()
    df = pd.read_excel(input_path) if ext in [".xlsx", ".xls"] else pd.read_csv(input_path)
    print(f"Loaded {len(df)} bugs")

    store = VersionMappingStore(Path(mapping_dir))

    sd_col = next(
        (c for c in df.columns if "short" in c.lower() and "desc" in c.lower()), None
    )
    if sd_col is None:
        sd_col = "Short Description"
    if sd_col not in df.columns:
        print(f"ERROR: Short Description column not found (expected: {sd_col})")
        print("Available columns:", list(df.columns))
        sys.exit(1)

    status_col = next((c for c in df.columns if c.lower() == "status"), None)

    print("Parsing Short Descriptions...")
    parsed = {
        "parsed_product": [],
        "parsed_version": [],
        "parsed_tags": [],
        "parsed_module_raw": [],   # original string from ECL e.g. "Auto Edit(Pet 02)"
        "parsed_module": [],       # normalised + sub-variant stripped e.g. "Auto Edit"
        "parsed_description": [],
        "module_category": [],
    }
    tag_cols = {t.lower().replace(" ", "_"): [] for t in KNOWN_TAGS}

    for _, row in df.iterrows():
        desc = str(row.get(sd_col, ""))

        version_hint = "unknown"
        vm = re.search(r"(\d+\.\d+(?:\.\d+)?)", desc)
        if vm:
            version_hint = vm.group(1)

        m = DESC_PATTERN.match(desc)
        if m:
            parsed["parsed_product"].append(m.group("product").strip())
            parsed["parsed_version"].append(m.group("version").strip())
            tags_raw = TAG_PATTERN.findall(m.group("tags"))
            parsed["parsed_tags"].append(tags_raw)
            raw_mod = m.group("module").strip()
            mod = normalize_module(
                raw_mod,
                version=version_hint,
                store=store,
                fuzzy_threshold=fuzzy_threshold,
            )
            parsed["parsed_module_raw"].append(raw_mod)
            parsed["parsed_module"].append(mod)
            parsed["parsed_description"].append(m.group("description").strip())
            parsed["module_category"].append(get_category(mod))
            for tk in tag_cols:
                tag_cols[tk].append(
                    any(t.lower().replace(" ", "_") == tk for t in tags_raw)
                )
        else:
            parsed["parsed_product"].append(None)
            parsed["parsed_version"].append(None)
            parsed["parsed_tags"].append([])
            parsed["parsed_module_raw"].append(None)
            parsed["parsed_module"].append(None)
            parsed["parsed_description"].append(desc)
            parsed["module_category"].append("Uncategorized")
            for tk in tag_cols:
                tag_cols[tk].append(False)

    for k, v in parsed.items():
        df[k] = v
    for tk, tv in tag_cols.items():
        df[f"tag_{tk}"] = tv

    # status_active
    if status_col:
        df["status_active"] = df[status_col].apply(classify_status)
        print("\nStatus classification:")
        print(f"  Active bugs:   {df['status_active'].sum():,}")
        print(f"  Inactive bugs: {(~df['status_active']).sum():,}")
    else:
        df["status_active"] = True
        print("\nWARNING: No 'Status' column found — all bugs marked active.")

    # Severity
    sev_col = next((c for c in df.columns if c.lower() == "severity"), None)
    if sev_col:
        sev_data = df[sev_col].apply(parse_severity)
        df["severity_num"] = sev_data.apply(lambda x: x[0])
        df["severity_weight"] = sev_data.apply(lambda x: x[1])
        df["severity_label"] = sev_data.apply(lambda x: x[2])

    # Priority label
    pri_col = next((c for c in df.columns if c.lower() == "priority"), None)
    if pri_col:
        df["priority_num"] = (
            pd.to_numeric(df[pri_col], errors="coerce").fillna(5).astype(int)
        )
        df["priority_label"] = df["priority_num"].map(PRIORITY_LABEL_MAP).fillna("N/A")

    # Repro probability
    repro_col = next(
        (c for c in df.columns if "repro" in c.lower() and "prob" in c.lower()), None
    )
    if repro_col:
        df["repro_rate"] = df[repro_col].apply(parse_repro)

    # Dates / days_to_close
    for dc in ["Create Date", "Closed Date"]:
        if dc in df.columns:
            df[dc] = pd.to_datetime(df[dc], errors="coerce")

    if "Create Date" in df.columns and "Closed Date" in df.columns:
        df["days_to_close"] = (df["Closed Date"] - df["Create Date"]).dt.days

    # builds_to_fix
    build_col = next(
        (c for c in df.columns if c.lower().strip() in ["build#", "build"]), None
    )
    close_build_col = next(
        (c for c in df.columns if c.lower().strip() in ["close build#", "close build"]),
        None,
    )
    if build_col and close_build_col:
        df["builds_to_fix"] = (
            pd.to_numeric(df[close_build_col], errors="coerce")
            - pd.to_numeric(df[build_col], errors="coerce")
        ).clip(lower=0)

    # Summary
    parsed_ok = df["parsed_module"].notna().sum()
    total = len(df)
    print("\nPARSING SUMMARY")
    print(f"Total bugs:            {total:,}")
    print(f"Successfully parsed:   {parsed_ok} ({parsed_ok / total * 100:.1f}%)")
    print(f"Unique modules:        {df['parsed_module'].dropna().nunique()}")
    if FUZZY_AVAILABLE:
        print(f"Fuzzy threshold used:  {fuzzy_threshold}%")

    total_pending = 0
    for p in store.versions_dir.glob("*_pending.json"):
        pending_data = store._load_json(p)
        unconfirmed = {k: v for k, v in pending_data.items() if not v.get("confirmed")}
        if unconfirmed:
            ver_name = p.stem.replace("_pending", "")
            print(
                f"\nPENDING review for version {ver_name} ({len(unconfirmed)} entries):"
            )
            for raw, info in list(unconfirmed.items())[:10]:
                print(f"  '{raw}' → suggested: '{info['suggested']}'")
            if len(unconfirmed) > 10:
                print(f"  ... and {len(unconfirmed) - 10} more (see {p})")
            total_pending += len(unconfirmed)

    print("\nTop 10 modules:")
    for mod, cnt in df["parsed_module"].value_counts().head(10).items():
        print(f"  {mod:35s} {cnt:>5}")

    uncat = (
        df[df["module_category"] == "Uncategorized"]["parsed_module"]
        .dropna()
        .unique()
    )
    if len(uncat) > 0:
        print(
            f"\nUNCATEGORIZED MODULES ({len(uncat)}) — add to MODULE_CATEGORIES or confirm in dashboard:"
        )
        for m in sorted(uncat):
            print(f"  - {m}")
    else:
        print("\n✅ No uncategorized modules!")

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\nSaved to: {output_path}")
    if total_pending > 0:
        print(
            f"⚠️  {total_pending} module strings pending dashboard review in {store.versions_dir}/"
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="PDR-I ECL Export Parser v2.3")
    parser.add_argument("input", help="Input Excel/CSV file")
    parser.add_argument("output", help="Output CSV file")
    parser.add_argument(
        "--mapping-dir",
        default="data/module_mappings",
        help="Directory for per-version mapping files (default: data/module_mappings)",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=85,
        help="Fuzzy match confidence threshold 0–100 (default: 85)",
    )
    args = parser.parse_args()
    parse_ecl_export(
        args.input,
        args.output,
        mapping_dir=args.mapping_dir,
        fuzzy_threshold=args.fuzzy_threshold,
    )


if __name__ == "__main__":
    main()