#!/usr/bin/env python3
"""PDR-I Visual Regression Testing v2.1
Usage: python visual_regression.py --compare --baselines baselines/ --current current/ --output results/"""
import os, sys, json, argparse, numpy as np
from PIL import Image, ImageChops
from pathlib import Path
from datetime import datetime

SIMILARITY_THRESHOLD = 0.95

def capture_screenshot(driver, name, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    fp = os.path.join(output_dir, f"{name}.png")
    driver.save_screenshot(fp)
    return fp

def compute_similarity(p1, p2):
    i1 = Image.open(p1).convert("RGB"); i2 = Image.open(p2).convert("RGB")
    if i1.size != i2.size: i2 = i2.resize(i1.size, Image.LANCZOS)
    d = np.array(ImageChops.difference(i1, i2))
    return round(np.sum(d < 10) / d.size, 4)

def highlight_differences(p1, p2, out):
    i1 = Image.open(p1).convert("RGB"); i2 = Image.open(p2).convert("RGB")
    if i1.size != i2.size: i2 = i2.resize(i1.size, Image.LANCZOS)
    d = np.array(ImageChops.difference(i1, i2))
    mask = np.any(d > 30, axis=2)
    ov = np.array(i2.copy()); ov[mask] = [255, 0, 0]
    bl = (0.7 * np.array(i2) + 0.3 * ov).astype(np.uint8)
    os.makedirs(os.path.dirname(out) if os.path.dirname(out) else ".", exist_ok=True)
    Image.fromarray(bl).save(out)
    return out

def compare_baselines(base_dir, curr_dir, res_dir):
    os.makedirs(res_dir, exist_ok=True)
    results = []
    bases = {f.stem: f for f in Path(base_dir).glob("*.png")}
    currs = {f.stem: f for f in Path(curr_dir).glob("*.png")}
    for name, bp in sorted(bases.items()):
        if name not in currs:
            results.append({"screen":name,"status":"MISSING","similarity":0,"diff":None}); continue
        sim = compute_similarity(str(bp), str(currs[name]))
        status = "PASS" if sim >= SIMILARITY_THRESHOLD else "FAIL"
        diff = None
        if status == "FAIL":
            diff = str(Path(res_dir)/f"{name}_diff.png")
            highlight_differences(str(bp), str(currs[name]), diff)
        results.append({"screen":name,"status":status,"similarity":sim,"diff":diff})
    for name in sorted(currs.keys()):
        if name not in bases:
            results.append({"screen":name,"status":"NEW","similarity":None,"diff":None})
    return results

def gen_report(results, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    total = len(results); passed = sum(1 for r in results if r["status"]=="PASS")
    failed = sum(1 for r in results if r["status"]=="FAIL")
    report = {"timestamp":datetime.now().isoformat(),"total":total,"passed":passed,"failed":failed,
              "pass_rate":round(passed/max(total,1)*100,1),"results":results}
    with open(os.path.join(out_dir,"visual_regression_report.json"),"w") as f: json.dump(report,f,indent=2)
    import pandas as pd
    pd.DataFrame(results).to_csv(os.path.join(out_dir,"visual_regression_results.csv"),index=False)
    print(f"Visual Regression: {passed} pass, {failed} fail, {total} total ({report['pass_rate']}%)")
    for r in results:
        if r["status"]=="FAIL": print(f"  FAIL: {r['screen']} sim={r['similarity']}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--compare",action="store_true")
    p.add_argument("--baselines",default="visual_baselines")
    p.add_argument("--current",default="visual_current")
    p.add_argument("--output",default="visual_results")
    a = p.parse_args()
    if a.compare: results = compare_baselines(a.baselines,a.current,a.output); gen_report(results,a.output)
    else: p.print_help()
if __name__=="__main__": main()
