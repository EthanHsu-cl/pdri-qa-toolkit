#!/usr/bin/env python3
"""Pending Module Review – PDR-I QA Toolkit

Standalone page for fuzzy module mappings:
- Shows all pending mappings from data/module_mappings/versions/*_pending.json
- Allows editing suggested canonical names
- Promotes selected mappings into permanent store:
    data/module_mappings/permanent/mappings_global.json
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# scripts/ is the parent of scripts/pages/; add it so we can import the
# canonical module list from the parser without duplicating it here.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from parse_ecl_export import (
        _CANONICAL_MODULES, MODULE_ALIASES, suggest_canonical,
    )
    CANONICAL_OPTIONS = sorted(set(_CANONICAL_MODULES) | set(MODULE_ALIASES.values()))
    _HAS_SUGGEST = True
except Exception:
    CANONICAL_OPTIONS = []
    _HAS_SUGGEST = False

st.set_page_config(
    page_title="Pending Module Review",
    layout="wide",
    page_icon="🧩",
)

st.title("🧩 Pending Module Review")

PRODUCTS_DIR = Path("data/products")
# Legacy single-product path (kept for backward compatibility)
_LEGACY_VERSIONS_DIR = Path("data/module_mappings/versions")
_LEGACY_PERMANENT_PATH = Path("data/module_mappings/permanent/mappings_global.json")


def _load_json(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


st.sidebar.header("Mapping Files")
st.sidebar.code(
    "data/products/<product>/module_mappings/versions/\n"
    "data/products/<product>/module_mappings/permanent/mappings_global.json"
)


def _discover_mapping_dirs():
    """Discover all per-product mapping version directories + legacy path."""
    dirs = {}  # product_slug -> versions_dir Path
    if PRODUCTS_DIR.exists():
        for prod_dir in sorted(PRODUCTS_DIR.iterdir()):
            ver_dir = prod_dir / "module_mappings" / "versions"
            if ver_dir.exists():
                dirs[prod_dir.name] = ver_dir
    # Also check staging (mapping files may not have been promoted yet)
    staging_products = Path("data/staging/products")
    if staging_products.exists():
        for prod_dir in sorted(staging_products.iterdir()):
            ver_dir = prod_dir / "module_mappings" / "versions"
            if ver_dir.exists() and prod_dir.name not in dirs:
                dirs[prod_dir.name] = ver_dir
    if _LEGACY_VERSIONS_DIR.exists() and not dirs:
        dirs["(legacy)"] = _LEGACY_VERSIONS_DIR
    return dirs


mapping_dirs = _discover_mapping_dirs()

if not mapping_dirs:
    st.info(
        "No mapping directory yet. Run parse_ecl_export.py at least once with fuzzy mapping enabled."
    )
    st.stop()

# ---------------------------------------------------------------------
# Load all pending entries
# ---------------------------------------------------------------------

pending_rows = []
for product_slug, versions_dir in mapping_dirs.items():
    for p in sorted(versions_dir.glob("*_pending.json")):
        version = p.stem.replace("_pending", "")
        try:
            data = _load_json(p)
        except Exception as e:
            st.warning(f"Failed to read {p.name}: {e}")
            continue

        for raw, info in data.items():
            if info.get("confirmed"):
                continue
            suggested = info.get("suggested", "")
            pending_rows.append(
                {
                    "Product": product_slug,
                    "Version": version,
                    "Raw Module": raw,
                    "Suggested Canonical": suggested,
                    "New Canonical (override)": "",
                }
            )

if not pending_rows:
    st.success("No pending mappings. All fuzzy matches are confirmed.")
    st.stop()

st.caption(
    "Review and edit suggested module names, then click **Promote selected** "
    "to add them to the permanent mapping store."
)

with st.expander("📖 What this page does — full guide", expanded=False):
    st.markdown("""
## 🧩 Pending Module Review — Complete Guide

When `parse_ecl_export.py` encounters a module name in the ECL export that does not exactly match
any known canonical module name, it runs a **fuzzy matcher** to find the closest known name and
saves the suggestion as a *pending* mapping. This page is where you review, edit, and approve those
suggestions before they become permanent.

---

### Why Module Name Normalisation Matters

The ECL export contains raw module names typed by testers — often with typos, abbreviations,
mixed capitalisation, or inconsistent spacing (e.g. `HQ Auido Denoise`, `AI story telling`,
`Export_video`, `ExportVideo`). If left unresolved, these become separate rows in every report,
splitting a module's bug count across multiple spellings and making it appear less risky than it is.

Once a mapping is promoted, every future run of `parse_ecl_export.py` will automatically
normalise that raw name to the canonical name — keeping all analysis consistent.

---

### 📋 The Editable Table

Each row in the table represents one unconfirmed fuzzy match:

| Column | Meaning |
|--------|---------|
| **Version** | The ECL version in which this raw module name first appeared |
| **Raw Module** | The exact module name string as it appeared in ECL |
| **Suggested Canonical** | The closest match found by `parse_ecl_export.py`'s fuzzy matcher |

**You can edit the Suggested Canonical column directly** before promoting. If the suggestion is wrong, type the correct canonical name. If the raw name is genuinely a new module not yet in the known list, type the new canonical name and then add it to `parse_ecl_export.py`'s module list afterwards.

---

### ✅ Promoting Mappings

1. **Select rows** to promote using the multiselect widget, the **Select ALL** button, or the **Bulk add by major** picker (adds all rows belonging to a given major version, e.g. all `16.x` entries).
2. Click **✅ Promote selected to permanent**.
3. Promoted mappings are written to `data/module_mappings/permanent/mappings_global.json` — the global lookup used by `parse_ecl_export.py`.
4. The promoted rows are removed from the pending file for that version, so they will not appear here again.
5. Re-run `parse_ecl_export.py` on the full ECL export to apply the new mappings retroactively to all historical bugs.

---

### 📁 File Locations

| File | Purpose |
|------|---------|
| `data/module_mappings/versions/<version>_pending.json` | Unreviewed fuzzy matches per ECL version |
| `data/module_mappings/versions/<version>_confirmed.json` | Confirmed mappings per version (written at promote time) |
| `data/module_mappings/permanent/mappings_global.json` | Master lookup used by `parse_ecl_export.py` on every run |

---

### When to Run This

- **After every new ECL export** parsed by `parse_ecl_export.py`. New versions frequently introduce new or misspelled module names.
- **Before running risk scoring** — accurate module names are required for correct I×P×D aggregation.
- Aim to clear the pending list to zero before each release review so risk scores are based on fully normalised data.
""")

# ---------------------------------------------------------------------
# Editable table
# ---------------------------------------------------------------------

df_pending = pd.DataFrame(pending_rows)
df_pending["__row_id"] = range(len(df_pending))  # stable ID 0..N-1

# --- AI Re-suggest + Copy / Download toolbar ---
blank_count = sum(
    1 for r in pending_rows
    if not str(r.get("Suggested Canonical", "")).strip()
)

import streamlit.components.v1 as components

col_ai, col_copy, col_dl = st.columns(3)

with col_ai:
    _ai_disabled = not _HAS_SUGGEST or blank_count == 0
    _ai_label = f"AI Re-suggest ({blank_count} blank)" if blank_count else "All rows have suggestions"
    ai_clicked = st.button(
        _ai_label, type="primary", disabled=_ai_disabled,
        help="Use Ollama embeddings + LLM to fill blank Suggested Canonical values.",
        use_container_width=True,
    )

_display_cols = ["Product", "Version", "Raw Module", "Suggested Canonical"]
_tsv_payload = df_pending[_display_cols].to_csv(sep="\t", index=False)
_csv_payload = df_pending[_display_cols].to_csv(index=False)

with col_copy:
    copy_clicked = st.button(
        "Copy table (TSV)",
        use_container_width=True,
        help="Copy the pending table as TSV to your clipboard.",
    )
    if copy_clicked:
        components.html(
            f"""<script>
                navigator.clipboard.writeText({json.dumps(_tsv_payload)});
            </script>""",
            height=0,
        )
        st.toast("Copied table to clipboard", icon="📋")

with col_dl:
    st.download_button(
        "Download CSV", _csv_payload,
        "pending_mappings.csv", "text/csv",
        use_container_width=True,
    )

# --- Run AI Re-suggest if clicked ---
if ai_clicked:
    progress = st.progress(0, text="Re-suggesting blank rows...")
    updated_count = 0
    blank_rows = [
        (i, r) for i, r in enumerate(pending_rows)
        if not str(r.get("Suggested Canonical", "")).strip()
    ]
    for step, (idx, row) in enumerate(blank_rows):
        product = row["Product"]
        version = row["Version"]
        raw = row["Raw Module"]
        cache_dir = mapping_dirs.get(product)
        if cache_dir is not None:
            cache_dir = cache_dir.parent
        pick, conf = suggest_canonical(raw, cache_dir=cache_dir)
        if pick:
            versions_dir = mapping_dirs.get(product)
            if versions_dir:
                p_path = versions_dir / f"{version}_pending.json"
                pdata = _load_json(p_path)
                if raw in pdata:
                    pdata[raw]["suggested"] = pick
                    _save_json(p_path, pdata)
                    updated_count += 1
        progress.progress((step + 1) / len(blank_rows),
                          text=f"Re-suggesting... {step + 1}/{len(blank_rows)}")
    progress.empty()
    if updated_count:
        st.success(f"Updated {updated_count} suggestions. Refreshing...")
        st.rerun()
    else:
        st.info("No new suggestions found (Ollama may be offline or all are genuinely unmatched).")

# --- Column config ---
if CANONICAL_OPTIONS:
    _column_config = {
        "Suggested Canonical": st.column_config.SelectboxColumn(
            "Suggested Canonical",
            options=CANONICAL_OPTIONS,
            required=False,
            help="Pick the canonical module. Empty = decide later.",
        ),
        "New Canonical (override)": st.column_config.TextColumn(
            "New Canonical (override)",
            help="If the correct canonical isn't in the dropdown, type it here — takes priority on promote.",
        ),
    }
else:
    _column_config = None
    st.warning(
        "Could not load the canonical module list from parse_ecl_export.py — "
        "'Suggested Canonical' shown as plain text. Check the import path."
    )

edited = st.data_editor(
    df_pending,
    column_config=_column_config,
    column_order=["Product", "Version", "Raw Module", "Suggested Canonical",
                   "New Canonical (override)"],
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
)

# ---------------------------------------------------------------------
# Bulk selection helpers
# ---------------------------------------------------------------------

df_pending = edited
df_pending["__row_id"] = df_pending["__row_id"].astype(int)

row_ids = df_pending["__row_id"].tolist()
all_indices = list(df_pending.index)

# This will store *indices* directly used by the multiselect widget
MULTI_KEY = "pending_multiselect_indices"
if MULTI_KEY not in st.session_state:
    st.session_state[MULTI_KEY] = []

# Derived: row_id -> index
id_to_index = {
    int(rid): idx for idx, rid in df_pending["__row_id"].items()
}
index_to_id = {
    idx: int(rid) for idx, rid in df_pending["__row_id"].items()
}

# Available versions + majors
versions_available = sorted(df_pending["Version"].astype(str).unique().tolist())
major_versions = sorted({v.split(".")[0] for v in versions_available})

# Callback helpers to mutate widget state directly
def select_all_rows():
    st.session_state[MULTI_KEY] = all_indices

def clear_all_rows():
    st.session_state[MULTI_KEY] = []

def add_major_rows():
    major = st.session_state.get("pending_major_ver", "—")
    if major == "—":
        return
    prefix = major + "."
    major_ids = df_pending[
        df_pending["Version"].astype(str).str.startswith(prefix)
    ]["__row_id"].tolist()
    major_indices = [id_to_index[rid] for rid in major_ids if rid in id_to_index]
    current = set(st.session_state.get(MULTI_KEY, []))
    st.session_state[MULTI_KEY] = sorted(current.union(major_indices))

col_all, col_major, col_clear = st.columns([1, 2, 1])

with col_all:
    st.button("Select ALL rows", key="btn_all", on_click=select_all_rows)

with col_major:
    st.selectbox(
        "Bulk add by major (16.x, 15.x, ...)",
        options=["—"] + major_versions,
        index=0,
        key="pending_major_ver",
    )
    st.button("Add this major", key="btn_major", on_click=add_major_rows)

with col_clear:
    st.button("Clear ALL selection", key="btn_clear", on_click=clear_all_rows)

def _format_label(i: int) -> str:
    return f"{df_pending.loc[i, 'Product']}:{df_pending.loc[i, 'Version']} – {df_pending.loc[i, 'Raw Module']}"

# Multiselect: its value is driven purely by st.session_state[MULTI_KEY]
selected_indices = st.multiselect(
    "Rows to promote",
    options=all_indices,
    # remove this line:
    # default=st.session_state[MULTI_KEY],
    format_func=_format_label,
    key=MULTI_KEY,
)

# Derived stable IDs for downstream logic
selected_row_ids = [index_to_id[i] for i in selected_indices]

# ---------------------------------------------------------------------
# Promote selected mappings
# ---------------------------------------------------------------------

col_promote, col_info = st.columns([1, 2])

with col_promote:
    if st.button("✅ Promote selected to permanent", type="primary"):
        if not selected_indices:
            st.warning("No rows selected.")
        else:
            # Group updates by product
            updated: dict[str, dict[str, dict]] = {}  # product -> {version -> {raw: canonical}}

            for i in selected_indices:
                row = edited.loc[i]
                product = str(row["Product"])
                version = str(row["Version"])
                raw = str(row["Raw Module"])
                # Override column wins when non-empty (user typed a canonical
                # that isn't in the dropdown). Otherwise take the dropdown pick.
                override = str(row.get("New Canonical (override)", "")).strip()
                canonical = override or str(row["Suggested Canonical"]).strip()
                if not canonical:
                    continue
                updated.setdefault(product, {}).setdefault(version, {})[raw] = canonical

            for product, ver_entries in updated.items():
                versions_dir = mapping_dirs.get(product)
                if versions_dir is None:
                    continue
                permanent_path = versions_dir.parent / "permanent" / "mappings_global.json"
                permanent = _load_json(permanent_path)

                for version, entries in ver_entries.items():
                    for raw, canonical in entries.items():
                        permanent[raw] = canonical

                        # Mark as confirmed
                        conf_path = versions_dir / f"{version}_confirmed.json"
                        conf_data = _load_json(conf_path)
                        conf_data[raw] = canonical
                        _save_json(conf_path, conf_data)

                    # Remove promoted entries from pending file
                    p_path = versions_dir / f"{version}_pending.json"
                    pdata = _load_json(p_path)
                    for raw in entries.keys():
                        pdata.pop(raw, None)
                    _save_json(p_path, pdata)

                _save_json(permanent_path, permanent)

            st.success(
                f"Promoted {len(selected_indices)} mappings to permanent store. "
                "Re-run parse_ecl_export.py to apply globally."
            )

with col_info:
    st.markdown(
        """
- **Permanent store**: `data/products/<product>/module_mappings/permanent/mappings_global.json`
- Future `parse_ecl_export.py` runs will use the canonical name immediately.
- You can return to this page anytime to review and clear new pending items.
"""
    )
