#!/usr/bin/env python3
"""PDR-I Auto-Tagger v2.1
Usage: python auto_tag_tests.py scored.csv --generate-skeletons tests/generated/ --summary"""
import os, re, sys, argparse, pandas as pd
from pathlib import Path

def module_to_filename(name):
    safe = re.sub(r"[^a-zA-Z0-9_]","_",name.lower())
    return f"test_{re.sub(r'_+','_',safe).strip('_')}.py"

def module_to_classname(name):
    words = re.sub(r"[^a-zA-Z0-9 ]"," ",name).split()
    return "Test"+"".join(w.capitalize() for w in words)

def generate_skeleton_tests(df, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    sev_map = {"p1 - critical":"CRITICAL","p2 - high":"NORMAL","p3 - medium":"MINOR","p4 - low":"TRIVIAL"}
    for _,row in df.iterrows():
        module = row["module"]; category = row.get("category","Unknown")
        quadrant = row.get("quadrant","P3 - Medium")
        q = quadrant.split(" ")[0].lower()
        risk = row.get("risk_score_final",0)
        iv = int(row.get("impact_score",3))
        pv = int(row.get("probability_score_auto",3)) if "probability_score_auto" in row else 3
        dv = int(row.get("detectability_score",3))
        sev = sev_map.get(q,"NORMAL"); cls = module_to_classname(module)
        fn = module_to_filename(module); vn = fn.replace(".py","").replace("test_","")
        lines = [f'#!/usr/bin/env python3',f'"""Auto-generated test for: {module}',
                 f'Category: {category} | Quadrant: {quadrant} | Risk: {risk:.0f} (I:{iv} x P:{pv} x D:{dv})',
                 f'TODO: Replace placeholder selectors."""',
                 f'import pytest\nimport allure\n',
                 f'@allure.suite("{category}")\n@allure.sub_suite("{module}")',
                 f'@allure.tag("{q.upper()}")\n@pytest.mark.{q}',
                 f'class {cls}:',f'    """{quadrant} tests for {module}."""\n',
                 f'    @allure.title("{module} - Screen Launch")',
                 f'    @allure.severity(allure.severity_level.{sev})',
                 f'    def test_screen_launches(self, driver):',f'        pass\n',
                 f'    @allure.title("{module} - Basic Functionality")',
                 f'    @allure.severity(allure.severity_level.{sev})',
                 f'    def test_basic_functionality(self, driver):',f'        pass\n',
                 f'    @allure.title("{module} - Visual Regression")',
                 f'    @allure.severity(allure.severity_level.NORMAL)',
                 f'    @pytest.mark.visual',
                 f'    def test_visual_regression(self, driver, visual_check):',
                 f'        # TODO: Navigate then call visual_check("{vn}")',f'        pass']
        with open(os.path.join(output_dir, fn),"w") as fout:
            fout.write("\n".join(lines)+"\n")
        count += 1
    print(f"Generated {count} test skeletons in {output_dir}/")

def tag_existing_tests(df, test_dir):
    mm = {r["module"].lower():r.get("quadrant","P3 - Medium").split(" ")[0].lower() for _,r in df.iterrows()}
    updated = 0
    for pf in Path(test_dir).glob("test_*.py"):
        content = pf.read_text()
        for mn,q in mm.items():
            if mn in content.lower():
                for old in re.findall(r"@pytest\.mark\.(p[1-4])", content):
                    if old!=q: content=content.replace(f"@pytest.mark.{old}",f"@pytest.mark.{q}"); updated+=1
                break
        pf.write_text(content)
    print(f"Updated {updated} markers in {test_dir}/")

def generate_summary(df, path="data/quadrant_summary.md"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# PDR-I Risk-Based Testing: Priority Assignment\n",f"**Modules: {len(df)}**\n"]
    for ql,desc in [("P1 - Critical","Every build"),("P2 - High","Daily"),
                    ("P3 - Medium","RC only"),("P4 - Low","RC if time")]:
        qd = df[df["quadrant"]==ql].sort_values("risk_score_final",ascending=False)
        lines.append(f"\n## {ql} ({len(qd)} modules) - {desc}\n")
        lines.append("| Module | Category | Score | IxPxD |")
        lines.append("|--------|----------|-------|-------|")
        for _,r in qd.iterrows():
            p = int(r.get("probability_score_auto",3)) if "probability_score_auto" in r else 3
            lines.append(f"| {r['module']} | {r.get('category','')} | {r['risk_score_final']:.0f} | {int(r['impact_score'])}x{p}x{int(r['detectability_score'])} |")
    with open(path,"w") as f: f.write("\n".join(lines))
    print(f"Summary saved to: {path}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_csv"); p.add_argument("--generate-skeletons",metavar="DIR")
    p.add_argument("--tag-existing",metavar="DIR"); p.add_argument("--summary",action="store_true")
    a = p.parse_args(); df = pd.read_csv(a.input_csv); print(f"Loaded {len(df)} modules")
    if a.generate_skeletons: generate_skeleton_tests(df, a.generate_skeletons)
    if a.tag_existing: tag_existing_tests(df, a.tag_existing)
    if a.summary: generate_summary(df)
    if not any([a.generate_skeletons,a.tag_existing,a.summary]): p.print_help()
if __name__=="__main__": main()