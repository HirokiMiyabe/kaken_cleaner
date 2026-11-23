"""2つの版の Kaken_Cleaner_fast.py を同じ XML にかけ、旧版の全列が新版で一致するか確かめる。

使い方:
    git show v0.1.0:Kaken_Cleaner_fast.py > old_Kaken_Cleaner_fast.py
    python tools/compare_versions.py old_Kaken_Cleaner_fast.py Kaken_Cleaner_fast.py --synthetic 5000
    python tools/compare_versions.py old_Kaken_Cleaner_fast.py Kaken_Cleaner_fast.py --xml KAKEN_xxx.xml

--synthetic N は乱数（seed 0）で合成した N 課題の XML を使う。KAKEN の実データは
リポジトリに入れないので、実データでの確認は --xml で手元のファイルを指定する。
旧版の列が新版にすべてあり、値が一致すれば mismatched cells: 0 と表示する。
"""
import argparse, importlib.util, json, random, tempfile
from pathlib import Path

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

rng = random.Random(0)

def member(seq, lang, name=True):
    n = f'<personalName><fullName>人{seq}{lang}</fullName></personalName>' if name else ''
    return (f'<member sequence="{seq}" eradCode="{rng.randint(1,9999)}">{n}'
            f'<institution>機関{seq}</institution><department>部局</department>'
            f'<jobTitle>教授</jobTitle></member>')

def field(nii, path, seq, table="saimoku"):
    return f'<field niiCode="{nii}" path="{path}" sequence="{seq}" fieldTable="{table}">分野{int(nii)}</field>'

def grant(i):
    r = rng.random
    fields = ""
    kind = rng.choice(["none", "2", "3", "4x2", "other", "one"])
    if kind == "2": fields = field("000079","000079","1") + field("003044","000079003044","1")
    elif kind == "3": fields = field("000079","000079","1")+field("000414","000079000414","1")+field("000415","000079000414000415","1")
    elif kind == "4x2":
        for seq, codes in (("1",(1,2,3,4)),("2",(1,5,6,7))):
            for d in range(1,5): fields += field(f"{codes[d-1]:06}", "".join(f"{c:06}" for c in codes[:d]), seq)
    elif kind == "other": fields = field("001894","001893001894","1","tokubetsu_kenkyu")
    elif kind == "one": fields = field("001350","001350","1")
    nja = rng.choice([1,1,2,3]); nen = rng.choice([0,1,2,3])
    ja = "".join(member(s, "ja", name=r() > .2) for s in range(1, nja+1))
    en = "".join(member(s, "en") for s in range(1, nen+1))
    dates = rng.choice([
        '<periodOfAward><startDate>2001-04-01</startDate><endDate>2004-03-31</endDate></periodOfAward>',
        '<periodOfAward searchStartFiscalYear="1990" searchEndFiscalYear="1992"><startFiscalYear>1990</startFiscalYear><endFiscalYear>1992</endFiscalYear></periodOfAward>',
        '<periodOfAward><startDate>2001-04</startDate></periodOfAward>', ''])
    created = rng.choice(['2003-05-01', '1987-01-01T00:00:00Z', ''])
    review = rng.choice(['', '<review_section niiCode="000767">区分A</review_section>',
                         '<review_section niiCode="1">A</review_section><review_section niiCode="2">B</review_section>'])
    kw = rng.choice(['', '<keywordList><keyword xml:lang="ja">k1</keyword></keywordList>', '<keywordList><keyword xml:lang="ja">k1</keyword><keyword xml:lang="ja">k2</keyword></keywordList>'])
    para = rng.choice(['', '<paragraphList type="abstract"><paragraph xml:lang="ja">概要</paragraph></paragraphList>',
                       '<paragraphList type="outline_of_research_initial"><paragraph xml:lang="ja">a</paragraph><paragraph xml:lang="ja">b</paragraph></paragraphList>'])
    cost = rng.choice(['', '<overallAwardAmount><directCost>3000</directCost><indirectCost>900</indirectCost><totalCost>3900</totalCost></overallAwardAmount>'])
    inst = rng.choice(['', '<institution niiCode="12601">大学A</institution>', '<institution niiCode="12601">大学A</institution><institution niiCode="12601">大学B</institution>'])
    prods = rng.choice(['', '<productList><product type="journal_article"><year>2002</year><author xml:lang="ja">甲・乙、丙</author><title>T</title></product></productList>',
                        '<productList><product type="presentation"><author xml:lang="ja">A,B</author></product><product type="book"><year>2003</year></product></productList>'])
    reps = rng.choice(['', '<paragraphList parentId="r1"><paragraph xml:lang="ja">報告</paragraph></paragraphList>'])
    status = rng.choice(['', '<projectStatus statusCode="project_closed" fiscalYear="1985"/>'])
    created_xml = f'<created>{created}</created>' if created else ''
    return (f'<grantAward awardNumber="{i:02}K{i:05}">{created_xml}<urlList><url>https://x/{i}</url></urlList>'
            f'<summary xml:lang="ja"><title>題{i}</title><category niiCode="000072" path="000072">基盤研究(C)</category>'
            f'{fields}{review}{inst}{ja}{status}{kw}{para}{dates}{cost}</summary>'
            f'<summary xml:lang="en"><title>T{i}</title>{en}</summary>{prods}{reps}</grantAward>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old"); ap.add_argument("new")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--xml"); g.add_argument("--synthetic", type=int)
    args = ap.parse_args()
    old, new = load("old_cleaner", args.old), load("new_cleaner", args.new)
    with tempfile.TemporaryDirectory() as d:
        if args.xml:
            src = Path(args.xml)
        else:
            src = Path(d) / "in.xml"
            src.write_text("<grantAwards>" + "".join(grant(i) for i in range(args.synthetic))
                           + "</grantAwards>", encoding="utf-8")
        outs = {}
        for label, mod in (("old", old), ("new", new)):
            out = Path(d) / f"{label}.jsonl"
            c = mod.KakenToTable(src)
            c.to_jsonlines(out, error_path=Path(d) / f"{label}.errors.tsv")
            rows = {}
            for line in out.read_text(encoding="utf-8").splitlines():
                row = json.loads(line); rows[row["awardNumber"]] = row
            outs[label] = (rows, c.failed)
    (o, of), (n, nf) = outs["old"], outs["new"]
    print(f"old rows={len(o)} failed={of}; new rows={len(n)} failed={nf}")
    only_old = set(o) - set(n)
    mism = {}
    for key in set(o) & set(n):
        for col, value in o[key].items():
            if n[key].get(col, object()) != value:
                mism[col] = mism.get(col, 0) + 1
    print(f"rows only in old: {len(only_old)}; rows only in new: {len(set(n) - set(o))}")
    print(f"mismatched cells: {sum(mism.values())} {mism if mism else ''}")

if __name__ == "__main__":
    main()
