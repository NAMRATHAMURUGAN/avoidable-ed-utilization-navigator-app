import pandas as pd
import numpy as np
from pathlib import Path
import gc

print("Starting Memory-Efficient Phase 2 Data Cleaning...")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
datasets_dir = PROJECT_ROOT / "datasets"
processed_dir = PROJECT_ROOT / "processed_data"
processed_dir.mkdir(exist_ok=True)

# 1. Beneficiary Cleaning
print("\n1. Cleaning Beneficiary Dataset...")
df_bene = pd.read_csv(datasets_dir / "beneficiary_2022.csv", sep="|", low_memory=False)
df_bene["BENE_ID"] = df_bene["BENE_ID"].astype(str).str.strip()
df_bene["BENE_BIRTH_DT"] = pd.to_datetime(df_bene["BENE_BIRTH_DT"], errors="coerce").dt.strftime("%Y-%m-%d")

if "BENE_DEATH_DT" in df_bene.columns:
    df_bene["BENE_DEATH_DT"] = pd.to_datetime(df_bene["BENE_DEATH_DT"], errors="coerce").dt.strftime("%Y-%m-%d")

df_bene = df_bene.drop_duplicates(subset=["BENE_ID"])
valid_bene_ids = set(df_bene["BENE_ID"])
print(f"Beneficiary records cleaned: {len(df_bene):,} rows.")

bene_out_path = processed_dir / "beneficiary_clean.csv"
df_bene.to_csv(bene_out_path, index=False)
print(f"Saved {bene_out_path} ({os.path.getsize(bene_out_path)/(1024*1024):.2f} MB)")

del df_bene
gc.collect()

# 2. Inpatient Claims Cleaning
print("\n2. Cleaning Inpatient Claims Dataset...")
df_inp = pd.read_csv(datasets_dir / "inpatient.csv", sep="|", low_memory=False)
df_inp["BENE_ID"] = df_inp["BENE_ID"].astype(str).str.strip()
df_inp["CLM_ID"] = df_inp["CLM_ID"].astype(str).str.strip()

for date_col in ["CLM_FROM_DT", "CLM_THRU_DT", "CLM_ADMSN_DT"]:
    if date_col in df_inp.columns:
        df_inp[date_col] = pd.to_datetime(df_inp[date_col], errors="coerce").dt.strftime("%Y-%m-%d")

for col in ["CLM_PMT_AMT", "CLM_TOT_CHRG_AMT", "NCH_BENE_IP_DDCTBL_AMT", "NCH_BENE_PTA_COINSRNC_LBLTY_AM"]:
    if col in df_inp.columns:
        df_inp[col] = pd.to_numeric(df_inp[col], errors="coerce").fillna(0.0)

for col in ["PRNCPAL_DGNS_CD", "ADMTG_DGNS_CD", "REV_CNTR", "PRVDR_NUM"]:
    if col in df_inp.columns:
        df_inp[col] = df_inp[col].astype(str).str.strip().replace({"nan": "", "None": ""})

inp_init = len(df_inp)
df_inp = df_inp[df_inp["BENE_ID"].isin(valid_bene_ids)]
print(f"Inpatient records cleaned: {len(df_inp):,} rows (Orphans dropped: {inp_init - len(df_inp)})")

inp_out_path = processed_dir / "inpatient_clean.csv"
df_inp.to_csv(inp_out_path, index=False)
print(f"Saved {inp_out_path} ({os.path.getsize(inp_out_path)/(1024*1024):.2f} MB)")

del df_inp
gc.collect()

# 3. Outpatient Claims Cleaning (Chunked / Memory Safe)
print("\n3. Cleaning Outpatient Claims Dataset in Chunks...")
outp_src_path = datasets_dir / "outpatient.csv"
outp_out_path = processed_dir / "outpatient_clean.csv"

# Identify core non-redundant columns to reduce RAM pressure
header_cols = pd.read_csv(outp_src_path, sep="|", nrows=1).columns.tolist()
# Keep core columns + diagnosis columns + cost columns
cols_to_keep = [c for c in header_cols if not (c.startswith("ICD_PRCDR") or c.startswith("PRCDR_DT") or c.startswith("CLM_POA_IND") or c.startswith("PTC_") or c.startswith("PTD_"))]

print(f"Outpatient columns reduced from {len(header_cols)} to {len(cols_to_keep)} essential columns for performance.")

chunk_size = 100000
first_chunk = True
total_outp_rows = 0
total_orphans = 0

for chunk in pd.read_csv(outp_src_path, sep="|", usecols=cols_to_keep, chunksize=chunk_size, low_memory=False):
    chunk["BENE_ID"] = chunk["BENE_ID"].astype(str).str.strip()
    chunk["CLM_ID"] = chunk["CLM_ID"].astype(str).str.strip()
    
    for date_col in ["CLM_FROM_DT", "CLM_THRU_DT"]:
        if date_col in chunk.columns:
            chunk[date_col] = pd.to_datetime(chunk[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
            
    for col in ["CLM_PMT_AMT", "CLM_TOT_CHRG_AMT", "NCH_BENE_PTB_DDCTBL_AMT", "NCH_BENE_PTB_COINSRNC_AMT"]:
        if col in chunk.columns:
            chunk[col] = pd.to_numeric(chunk[col], errors="coerce").fillna(0.0)
            
    for col in ["PRNCPAL_DGNS_CD", "REV_CNTR", "PRVDR_NUM", "HCPCS_CD"]:
        if col in chunk.columns:
            chunk[col] = chunk[col].astype(str).str.strip().replace({"nan": "", "None": ""})
            
    init_chunk_len = len(chunk)
    chunk = chunk[chunk["BENE_ID"].isin(valid_bene_ids)]
    total_orphans += (init_chunk_len - len(chunk))
    total_outp_rows += len(chunk)
    
    if first_chunk:
        chunk.to_csv(outp_out_path, mode="w", index=False)
        first_chunk = False
    else:
        chunk.to_csv(outp_out_path, mode="a", header=False, index=False)
        
    gc.collect()

print(f"Outpatient records cleaned: {total_outp_rows:,} rows saved (Orphans dropped: {total_orphans}).")
print(f"Saved {outp_out_path} ({os.path.getsize(outp_out_path)/(1024*1024):.2f} MB)")
print("\nMemory-Efficient Phase 2 Data Cleaning Complete!")
