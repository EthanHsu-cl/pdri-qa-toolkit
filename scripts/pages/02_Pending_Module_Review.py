#!/usr/bin/env python3
"""Pending Module Review – PDR-I QA Toolkit

Standalone page for fuzzy module mappings:
- Shows all pending mappings from data/module_mappings/versions/*_pending.json
- Allows editing suggested canonical names
- Promotes selected mappings into permanent store:
    data/module_mappings/permanent/mappings_global.json
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Pending Module Review",
    layout="wide",
    page_icon="🧩",
)

st.title("🧩 Pending Module Review")

MAPPING_VERSIONS_DIR = Path("data/module_mappings/versions")
MAPPING_PERMANENT_PATH = Path("data/module_mappings/permanent/mappings_global.json")


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
    "data/module_mappings/versions/\n"
    "data/module_mappings/permanent/mappings_global.json"
)

if not MAPPING_VERSIONS_DIR.exists():
    st.info(
        "No mapping directory yet. Run parse_ecl_export.py at least once with fuzzy mapping enabled."
    )
    st.stop()

# ---------------------------------------------------------------------
# Load all pending entries
# ---------------------------------------------------------------------

pending_rows = []
for p in sorted(MAPPING_VERSIONS_DIR.glob("*_pending.json")):
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
                "Version": version,
                "Raw Module": raw,
                "Suggested Canonical": suggested,
            }
        )

if not pending_rows:
    st.success("No pending mappings. All fuzzy matches are confirmed.")
    st.stop()

st.caption(
    "Review and edit suggested module names, then click **Promote selected** "
    "to add them to the permanent mapping store."
)

# ---------------------------------------------------------------------
# Editable table
# ---------------------------------------------------------------------

df_pending = pd.DataFrame(pending_rows)
df_pending["__row_id"] = range(len(df_pending))  # stable ID 0..N-1

edited = st.data_editor(
    df_pending,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
)

# ---------------------------------------------------------------------
# Bulk selection helpers (stable, no direct widget state writes)
# ---------------------------------------------------------------------

df_pending = edited
df_pending["__row_id"] = df_pending["__row_id"].astype(int)

row_ids = df_pending["__row_id"].tolist()
key_selected = "pending_selected_ids"

if key_selected not in st.session_state:
    st.session_state[key_selected] = []

# Available versions + majors
versions_available = sorted(df_pending["Version"].astype(str).unique().tolist())
major_versions = sorted({v.split(".")[0] for v in versions_available})

col_all, col_major, col_clear = st.columns([1, 2, 1])

with col_all:
    if st.button("Select ALL rows", key="btn_all"):
        # Select all row_ids
        st.session_state[key_selected] = row_ids

with col_major:
    major = st.selectbox(
        "Bulk add by major (16.x, 15.x, ...)",
        options=["—"] + major_versions,
        index=0,
        key="pending_major_ver",
    )
    if st.button("Add this major", key="btn_major"):
        if major != "—":
            prefix = major + "."
            major_ids = df_pending[
                df_pending["Version"].astype(str).str.startswith(prefix)
            ]["__row_id"].tolist()
            current = set(st.session_state[key_selected])
            st.session_state[key_selected] = sorted(current.union(major_ids))

with col_clear:
    if st.button("Clear ALL selection", key="btn_clear"):
        st.session_state[key_selected] = []

# Map row_id -> current index
id_to_index = {
    int(rid): idx for idx, rid in df_pending["__row_id"].items()
}

# Compute indices from stored IDs
indices_from_ids = [
    id_to_index[rid]
    for rid in st.session_state[key_selected]
    if rid in id_to_index
]


def _format_label(i: int) -> str:
    return f"{df_pending.loc[i, 'Version']} – {df_pending.loc[i, 'Raw Module']}"

# Use selection from session_state as the default, always
multiselect_default = indices_from_ids

selected_indices = st.multiselect(
    "Rows to promote",
    options=list(df_pending.index),
    default=multiselect_default,
    format_func=_format_label,
    key="pending_multiselect",
)

# After widget, update our own selection state from its current value
st.session_state[key_selected] = [
    int(df_pending.loc[i, "__row_id"]) for i in selected_indices
]

# ---------------------------------------------------------------------
# Promote selected mappings
# ---------------------------------------------------------------------

col_promote, col_info = st.columns([1, 2])

with col_promote:
    if st.button("✅ Promote selected to permanent", type="primary"):
        if not selected_indices:
            st.warning("No rows selected.")
        else:
            permanent = _load_json(MAPPING_PERMANENT_PATH)
            updated_versions: dict[str, dict] = {}

            for i in selected_indices:
                row = edited.loc[i]
                version = str(row["Version"])
                raw = str(row["Raw Module"])
                canonical = str(row["Suggested Canonical"]).strip()
                if not canonical:
                    continue

                # Update permanent store
                permanent[raw] = canonical

                # Also mark as confirmed in this version's _confirmed.json
                conf_path = MAPPING_VERSIONS_DIR / f"{version}_confirmed.json"
                conf_data = _load_json(conf_path)
                conf_data[raw] = canonical
                _save_json(conf_path, conf_data)
                updated_versions.setdefault(version, {})[raw] = canonical

            _save_json(MAPPING_PERMANENT_PATH, permanent)

            # Remove promoted entries from pending files
            for version, entries in updated_versions.items():
                p_path = MAPPING_VERSIONS_DIR / f"{version}_pending.json"
                pdata = _load_json(p_path)
                for raw in entries.keys():
                    if raw in pdata:
                        pdata.pop(raw, None)
                _save_json(p_path, pdata)

            st.success(
                f"Promoted {len(selected_indices)} mappings to permanent store. "
                "Re-run parse_ecl_export.py to apply globally."
            )

with col_info:
    st.markdown(
        """
- **Permanent store**: `data/module_mappings/permanent/mappings_global.json`  
- Future `parse_ecl_export.py` runs will use the canonical name immediately.  
- You can return to this page anytime to review and clear new pending items.
"""
    )
