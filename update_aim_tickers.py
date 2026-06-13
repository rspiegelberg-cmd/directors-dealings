"""Update tickers_meta with AIM benchmark for all FTSE AIM All-Share constituents.

Ticker list scraped from lse.co.uk FTSE AIM All-Share constituents page, 2026-06-04.
Cross-references against the project DB and sets:
  - is_aim = 1
  - benchmark_symbol = '^AIM'
for every matching ticker found in both sources.

Zone B -- writes to DB. Run from project root:
    python update_aim_tickers.py [--dry-run]
"""
import argparse, sqlite3, sys

# Full FTSE AIM All-Share constituent tickers from lse.co.uk (2026-06-04).
AIM_TICKERS = {
    "4BB","80M","88E","AAU","AAZ","ABDP","ABDX","ACRM","ACSO","ADF",
    "ADVT","AEO","AET","AIEA","ALBA","ALL","ALT","ALU","AMRQ","AMS",
    "ANCR","ANG","ANGS","ANIC","ANP","AOM","AOTI","APTA","ARBB","ARC",
    "ARCM","AREC","ARK","ARS","ART","AST","ASY","AT.","ATM","ATOM",
    "AURA","AURR","AUTG","AVCT","AVG","AXL","AXS","AYM","B90","BANK",
    "BBSN","BEM","BGO","BHL","BIG","BILN","BIRD","BKS","BLOE","BLU",
    "BOKU","BOOM","BOR","BPM","BRCK","BRK","BUR","BVXP","BZT","CAM",
    "CAML","CASP","CAV","CBOX","CCT","CDGP","CER","CGNR","CHAR","CHH",
    "CHRT","CIC","CKT","CLA","CLBS","CLCO","CLON","CLX","CMCL","CMET",
    "CML","CNC","CNS","CNSL","CODE","COG","COM","CORA","CPH2","CPP",
    "CPX","CRCL","CRDL","CREO","CRPR","CRS","CRTA","CRTX","CRU","CRW",
    "CSSG","CTA","CTAI","CTG","CTL","CVSG","CYAN","DATA","DBOX","DELT",
    "DEVO","DFCH","DIAL","DIS","DKL","DNM","DOTD","DPP","DSG","DSW",
    "DUKE","DXRX","EAAS","EAH","EARN","EBQ","ECO","ECOB","ECR","EDEN",
    "EEE","EGT","EKF","ELCO","ELIX","EMAN","EME","EMH","EML","EMR",
    "EMVC","ENET","ENSI","ENW","EOG","EPP","ESO","ESYS","EVPL","EXR",
    "EYE","FAB","FARN","FDBK","FDEV","FEN","FEVR","FIH","FIN","FIPP",
    "FKE","FLO","FMET","FNTL","FNX","FPO","FRAN","FRG","FRP","FTC",
    "FUM","G4M","GAL","GAMA","GATC","GBG","GCM","GDP","GDR","GELN",
    "GEM","GENI","GETB","GFIN","GFM","GGP","GHH","GLR","GMET","GMR",
    "GNIP","GRL","GROC","GRP","GTC","GTLY","GUN","GWMO","HAYD","HCM",
    "HDD","HE1","HERC","HEX","HMI","HSP","HUD","HUW","HVO","IES",
    "IGE","IGP","IGR","IHC","IKA","IMM","ING","INSG","IOF","IOM",
    "IPX","IQE","ITIM","ITM","ITX","IXI","JAN","JDG","JET2","JHD",
    "JIM","JLP","JNEO","JOG","JSG","KDNC","KDR","KEFI","KETL","KEYS",
    "KGH","KIST","KMK","KOD","KOO","KP2","KRS","KZG","LBG","LDG",
    "LEX","LIKE","LINV","LIT","LND","LORD","LPA","LST","LTHM","MAB1",
    "MAC","MAFL","MAI","MANO","MATD","MBH","MBO","MDZ","MERC","MET1",
    "MEX","MFAI","MFX","MIDW","MIND","MIRI","MKA","MLVN","MPAC","MPE",
    "MPL","MRK","MSI","MTC","MTEC","MTL","MWE","MYX","NAH","NAR",
    "NBB","NCYT","NET","NEXS","NFG","NICL","NIOX","NTBR","NTVO","NWF",
    "NWT","NXQ","OBD","OBI","OMG","OMI","OMIP","ONWD","OPT","OPTI",
    "ORCA","ORCH","ORCP","ORR","PAF","PANR","PAT","PCIP","PEB","PEBB",
    "PEEL","PEG","PEN","PET","PGH","PHE","PHSC","PIP","PLSR","PMG",
    "PMI","PMP","POLB","POLR","POS","POW","PREM","PRIM","PRM","PTAL",
    "PULS","PXC","PXEN","PXS","PYC","QBT","QED","QTX","RBD","RBN",
    "RCN","RDT","REAT","RENX","REVB","RFX","RKH","RLE","RMR","RNWH",
    "ROAD","ROCK","RRR","RST","RTC","RUA","RWS","SAA","SAAS","SAG",
    "SAL","SALT","SAR","SAV","SBDS","SBTX","SCLP","SDG","SDI","SEA",
    "SEE","SEED","SEEN","SFT","SHOE","SKA","SKL","SLP","SML","SNDA",
    "SNT","SNX","SOLI","SOM","SORT","SOS","SOU","SOUC","SPEC","SPR",
    "SPSY","SQZ","SRB","SRC","SRES","SRT","SSTY","STAF","STAR","STCM",
    "STG","STX","SUN","SUP","SWG","SYM","SYN","SYS","SYS1","TAM",
    "TAVI","TBLD","TCF","TEAM","TEK","TENG","TERN","TFW","TGP","THR",
    "THRU","THX","TIDE","TIG","TIME","TMG","TMO","TMT","TND","TON",
    "TPFG","TPX","TRAC","TRB","TRCS","TRLS","TRP","TRT","TRU","TST",
    "TSTL","TUN","TUNE","TXP","TYM","UFO","UJO","UKOG","UOG","UPR",
    "URU","VAL","VANL","VAST","VCP","VEL","VIC","VINO","VLE","VLG",
    "VLX","VNET","VRCI","VTU","W7L","WATR","WINE","WINK","WJG","WPHO",
    "WRKS","WSBN","WTE","WYN","XPF","XSG","XTR","YCA","YNGA","YNGN",
    "YOU","YU.","ZAM","ZED","ZIN","ZIOC","ZNWD","ZOO","ZPHR",
    # Also include MET (METIR) from page header
    "MET",
}

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

conn = sqlite3.connect('.data/directors.db')
conn.row_factory = sqlite3.Row

# All tickers in our DB
db_tickers = {
    r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM tickers_meta"
    ).fetchall()
}

matches = sorted(AIM_TICKERS & db_tickers)
print(f"AIM constituents:     {len(AIM_TICKERS)}")
print(f"Tickers in our DB:    {len(db_tickers)}")
print(f"Overlap (AIM in DB):  {len(matches)}")
print()

if args.dry_run:
    print("DRY RUN -- would update:")
    for t in matches:
        row = conn.execute(
            "SELECT is_aim, benchmark_symbol FROM tickers_meta WHERE ticker=?", (t,)
        ).fetchone()
        status = "already correct" if (row["is_aim"] and row["benchmark_symbol"] == "^AIM") else "NEEDS UPDATE"
        print(f"  {t:<8} is_aim={row['is_aim']} bench={row['benchmark_symbol']} -> {status}")
else:
    updated = 0
    for t in matches:
        conn.execute(
            "UPDATE tickers_meta SET is_aim=1, benchmark_symbol='^AIM' WHERE ticker=?",
            (t,),
        )
        if conn.execute(
            "SELECT changes()"
        ).fetchone()[0]:
            updated += 1
    conn.commit()
    print(f"Updated {updated} tickers to is_aim=1 / benchmark_symbol='^AIM'.")
    print()
    print("Updated tickers:")
    for t in matches:
        print(f"  {t}")

conn.close()

if not args.dry_run:
    print()
    print("Next: run python check_aim_tickers.py to verify, then backfill_benchmarks.py")
