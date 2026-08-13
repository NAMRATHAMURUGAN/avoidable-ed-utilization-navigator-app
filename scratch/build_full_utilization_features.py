import pandas as pd
import numpy as np
from pathlib import Path
import gc

print("Starting Full Member Utilization Feature Aggregation...")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
processed_dir = PROJECT_ROOT / "processed_data"

path_bene = processed_dir / "beneficiary_clean.csv"
path_inp = processed_dir / "inpatient_clean.csv"
path_outp = processed_dir / "outpatient_clean.csv"

# 1. Profile Features
print("Loading Beneficiary Profiles...")
df_bene = pd.read_csv(path_bene, low_memory=False)
df_bene['BENE_ID'] = df_bene['BENE_ID'].astype(str).str.strip()

age_col = 'AGE_AT_END_REF_YR' if 'AGE_AT_END_REF_YR' in df_bene.columns else 'BENE_BIRTH_DT'
if age_col == 'AGE_AT_END_REF_YR':
    df_bene['age'] = pd.to_numeric(df_bene['AGE_AT_END_REF_YR'], errors='coerce').fillna(65).astype(int)
else:
    df_bene['age'] = (2022 - pd.to_datetime(df_bene['BENE_BIRTH_DT'], errors='coerce').dt.year).fillna(65).astype(int)

df_bene['gender'] = df_bene['SEX_IDENT_CD'].astype(str).map({'1': 'Male', '2': 'Female'}).fillna('Unknown')
df_bene['dual_eligibility_months'] = pd.to_numeric(df_bene.get('DUAL_ELGBL_MONS', 0), errors='coerce').fillna(0).astype(int)

chronic_cols = [c for c in df_bene.columns if c.startswith('SP_') or c.startswith('CHRONIC_')]
df_bene['chronic_condition_count'] = (df_bene[chronic_cols] == 1).sum(axis=1) if chronic_cols else 0

profile_df = df_bene[['BENE_ID', 'age', 'gender', 'dual_eligibility_months', 'chronic_condition_count']].copy()
del df_bene
gc.collect()

# 2. Inpatient Features
print("Processing Inpatient Claims...")
df_inp = pd.read_csv(path_inp, low_memory=False)
df_inp['BENE_ID'] = df_inp['BENE_ID'].astype(str).str.strip()
df_inp['CLM_ID'] = df_inp['CLM_ID'].astype(str).str.strip()
df_inp['REV_CNTR'] = df_inp['REV_CNTR'].astype(str).str.strip()
df_inp['is_ed'] = df_inp['REV_CNTR'].str.contains('0450|450', na=False)

inp_agg = df_inp.groupby('BENE_ID').agg(
    inpatient_visit_count=('CLM_ID', 'nunique'),
    inp_ed_visit_count=('is_ed', 'sum'),
    inpatient_total_cost=('CLM_PMT_AMT', 'sum'),
    inp_provider_count=('PRVDR_NUM', 'nunique')
).reset_index()

inp_ed_costs = df_inp[df_inp['is_ed']].groupby('BENE_ID')['CLM_PMT_AMT'].sum().reset_index(name='inp_ed_cost')
inp_agg = inp_agg.merge(inp_ed_costs, on='BENE_ID', how='left').fillna({'inp_ed_cost': 0.0})

del df_inp
gc.collect()

# 3. Outpatient Features
print("Processing Outpatient Claims in Chunks...")
outp_aggs = []

for chunk in pd.read_csv(path_outp, chunksize=100000, low_memory=False):
    chunk['BENE_ID'] = chunk['BENE_ID'].astype(str).str.strip()
    chunk['CLM_ID'] = chunk['CLM_ID'].astype(str).str.strip()
    chunk['REV_CNTR'] = chunk['REV_CNTR'].astype(str).str.strip()
    chunk['is_ed'] = chunk['REV_CNTR'].str.contains('0450|450', na=False)
    
    c_agg = chunk.groupby('BENE_ID').agg(
        outpatient_visit_count=('CLM_ID', 'nunique'),
        outp_ed_visit_count=('is_ed', 'sum'),
        outpatient_total_cost=('CLM_PMT_AMT', 'sum'),
        outp_provider_count=('PRVDR_NUM', 'nunique')
    ).reset_index()
    
    c_ed_cost = chunk[chunk['is_ed']].groupby('BENE_ID')['CLM_PMT_AMT'].sum().reset_index(name='outp_ed_cost')
    c_agg = c_agg.merge(c_ed_cost, on='BENE_ID', how='left').fillna({'outp_ed_cost': 0.0})
    outp_aggs.append(c_agg)

outp_df = pd.concat(outp_aggs, ignore_index=True)
outp_agg = outp_df.groupby('BENE_ID').agg(
    outpatient_visit_count=('outpatient_visit_count', 'sum'),
    outp_ed_visit_count=('outp_ed_visit_count', 'sum'),
    outpatient_total_cost=('outpatient_total_cost', 'sum'),
    outp_ed_cost=('outp_ed_cost', 'sum'),
    outp_provider_count=('outp_provider_count', 'max')
).reset_index()

del outp_df
gc.collect()

# 4. Consolidate Final Feature Vector
print("Consolidating Member Feature Vector...")
features = profile_df.merge(inp_agg, on='BENE_ID', how='left').merge(outp_agg, on='BENE_ID', how='left').fillna(0)

features['inpatient_visit_count'] = features['inpatient_visit_count'].astype(int)
features['outpatient_visit_count'] = features['outpatient_visit_count'].astype(int)
features['ed_visit_count'] = (features['inp_ed_visit_count'] + features['outp_ed_visit_count']).astype(int)

features['total_claim_payment_amount'] = (features['inpatient_total_cost'] + features['outpatient_total_cost']).round(2)
features['total_ed_related_cost'] = (features['inp_ed_cost'] + features['outp_ed_cost']).round(2)

total_encounters = features['inpatient_visit_count'] + features['outpatient_visit_count']
features['average_claim_cost'] = np.where(total_encounters > 0, (features['total_claim_payment_amount'] / total_encounters).round(2), 0.0)

features['provider_count'] = (features['inp_provider_count'] + features['outp_provider_count']).astype(int)

# Final Column Cleanliness
cols = [
    'BENE_ID', 'age', 'gender', 'dual_eligibility_months', 'chronic_condition_count',
    'inpatient_visit_count', 'inpatient_total_cost', 'outpatient_visit_count', 'outpatient_total_cost',
    'ed_visit_count', 'total_claim_payment_amount', 'total_ed_related_cost', 'average_claim_cost', 'provider_count'
]
features = features[cols]

out_path = processed_dir / "utilization_features.csv"
features.to_csv(out_path, index=False)

print(f"Successfully generated {out_path} ({os.path.getsize(out_path)/(1024*1024):.2f} MB)")
print(f"Total Members Features Aggregated: {len(features):,}")
print(f"Columns Created ({len(features.columns)}): {list(features.columns)}")
print("Phase 3 Full Feature Engineering Complete!")
