#!/usr/bin/env python3
"""PDR-I AI Risk Scorer v2.3 - urllib3 fixed, default model qwen3:7b

Behavior:

Given a combined risk register (ALL versions), this script will:

  - Score the combined file → risk_register_scored_all.csv
  - Score each per-version risk register found in the same directory:
        risk_register_<version>.csv
    and save:
        risk_register_scored_<version>.csv

No extra arguments needed.

Usage:
  python ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider heuristic
"""
import warnings
warnings.filterwarnings("ignore", message="urllib3.*OpenSSL")
warnings.filterwarnings("ignore", message="urllib3.*LibreSSL")

import os, json, re, argparse, time, glob
import pandas as pd, numpy as np
import urllib.request


IMPACT_OVERRIDES = {
    "export":5,"produced video":5,"ai storytelling":5,"auto edit":5,"project":5,
    "new project":5,"auto edit project":4,"image to video":4,"text to video":4,
    "ai art":4,"ai music generator":4,"auto captions":4,"video enhance":4,
    "camera":4,"trim":4,"split":3,"settings":1,"preference":1,
    "tutorials & tips":1,"credit":1,"more":1,"my artwork":1,
    "trending":2,"vip benefit page":3,
}


def score_heuristic(row):
    mod = str(row.get("module","")).lower()
    crit = float(row.get("critical_count",0))
    repro = float(row.get("avg_repro_rate",0.5)) if "avg_repro_rate" in row.index else 0.5
    at_rate = float(row.get("automation_catch_rate",0))
    reg_rate = float(row.get("regression_rate",0))
    impact = IMPACT_OVERRIDES.get(mod, 3)
    if crit >= 10: impact = min(impact+1, 5)
    if crit >= 5 and impact < 4: impact = 4
    detect = 3
    if at_rate > 0.1: detect -= 1
    if at_rate > 0.3: detect -= 1
    if at_rate == 0: detect += 1
    if repro < 0.5: detect += 1
    if reg_rate > 0.15: detect += 1
    detect = max(1, min(5, detect))
    reasoning = f"I={impact}(domain,crit={int(crit)}) D={detect}(AT={at_rate:.0%},repro={repro:.0%})"
    return impact, detect, "heuristic", reasoning


def score_ollama(row, model="llama3.1"):
    mod = row.get("module", "Unknown")

    prompt = (
        "You are a QA risk analyst.\n"
        "Return ONLY a JSON object, no extra text.\n"
        'Schema: {"impact": int (1-5), "detectability": int (1-5), "reasoning": string}\n\n'
        f"Module: {mod}\n"
        f"Category: {row.get('category', '')}\n"
        f"TotalBugs: {row.get('total_bugs', 0)}\n"
        f"Critical: {row.get('critical_count', 0)}\n"
        f"Major: {row.get('major_count', 0)}\n"
        f"RegressionRate: {row.get('regression_rate', 0):.3f}\n"
        f"AutomationCatchRate: {row.get('automation_catch_rate', 0):.3f}\n"
    )

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }).encode()

    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urllib.request.urlopen(req, timeout=120)
        raw = resp.read().decode(errors="replace")
        envelope = json.loads(raw)
        text = envelope.get("response", "").strip()

        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not m:
                raise
            obj = json.loads(m.group(0))

        impact = int(obj.get("impact", 3))
        detect = int(obj.get("detectability", 3))
        reason = str(obj.get("reasoning", ""))

        impact = max(1, min(5, impact))
        detect = max(1, min(5, detect))
        return impact, detect, "ollama", reason

    except Exception as e:
        print(f" Ollama failed for {mod} (model={model}): {e}")
        return score_heuristic(row)


def score_openai(row):
    api_key = os.environ.get("OPENAI_API_KEY","")
    if not api_key:
        return score_heuristic(row)

    mod = row.get("module","Unknown")
    msgs = [
        {"role":"system","content":"You are a QA risk analyst. Return only JSON."},
        {"role":"user","content":(
            f"Score '{mod}' Bugs:{row.get('total_bugs',0)} Crit:{row.get('critical_count',0)} "
            'Return JSON: {"impact":<1-5>,"detectability":<1-5>,"reasoning":"..."}'
        )},
    ]
    payload = json.dumps({
        "model":"gpt-4o-mini",
        "messages":msgs,
        "temperature":0.2
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type":"application/json",
            "Authorization":f"Bearer {api_key}",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        text = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        jm = re.search(r"\{[^}]+\}", text)
        if jm:
            obj = json.loads(jm.group())
            return (
                int(obj.get("impact",3)),
                int(obj.get("detectability",3)),
                "openai",
                obj.get("reasoning",""),
            )
    except Exception as e:
        print(f" OpenAI failed for {mod}: {e}")
    return score_heuristic(row)


def assign_quadrant(score):
    if score >= 60: return "Q4 - Test First"
    if score >= 30: return "Q3 - Test Second"
    if score >= 10: return "Q2 - Test Third"
    return "Q1 - Test Last"


def score_file(in_csv: str, out_csv: str, provider: str, model: str):
    df = pd.read_csv(in_csv)
    print(f"\nScoring {len(df)} modules from {in_csv}")

    if "parsed_module" not in df.columns and "module" in df.columns:
        df["parsed_module"] = df["module"]

    impacts, detects, methods, reasons = [], [], [], []
    for i, (_, row) in enumerate(df.iterrows()):
        if provider == "heuristic":
            r = score_heuristic(row)
        elif provider == "ollama":
            r = score_ollama(row, model=model)
            if i % 10 == 0:
                print(f"  Progress: {i+1}/{len(df)}")
        else:
            r = score_openai(row)
            time.sleep(0.5)
        impacts.append(r[0]); detects.append(r[1]); methods.append(r[2]); reasons.append(r[3])

    df["impact_score"] = impacts
    df["detectability_score"] = detects
    df["scoring_method"] = methods
    df["ai_reasoning"] = reasons

    prob = df["probability_score_auto"] if "probability_score_auto" in df.columns else 3
    df["risk_score_final"] = df["impact_score"] * prob * df["detectability_score"]
    df["quadrant"] = df["risk_score_final"].apply(assign_quadrant)
    df = df.sort_values("risk_score_final", ascending=False)

    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  Saved to: {out_csv}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_csv")   # risk_register_all.csv
    p.add_argument("output_csv")  # risk_register_scored_all.csv
    p.add_argument("--provider", choices=["heuristic","ollama","openai"], default="heuristic")
    p.add_argument("--model", default="qwen3:7b")
    a = p.parse_args()

    base_dir = os.path.dirname(a.input_csv) or "."
    base_out_dir = os.path.dirname(a.output_csv) or "."
    base_out_name = os.path.basename(a.output_csv)

    # 1) Score combined file
    score_file(a.input_csv, a.output_csv, a.provider, a.model)

    # 2) Score each per-version risk_register_<ver>.csv
    pattern = os.path.join(base_dir, "risk_register_*.csv")
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        if name == os.path.basename(a.input_csv):
            continue  # skip the combined file
        ver_part = name.replace("risk_register_", "").replace(".csv", "")
        out_name = f"risk_register_scored_{ver_part}.csv"
        out_path = os.path.join(base_out_dir, out_name)
        score_file(path, out_path, a.provider, a.model)


if __name__=="__main__":
    main()
