from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# ✅ Enable CORS so your HTML or Bubble app can call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # you can replace "*" with your specific domains later
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PatientCase(BaseModel):
    wound_grade: int
    ischemia_grade: int
    infection_grade: int
    ABI: Optional[float] = None
    Toe_pressure: Optional[float] = None
    TcPO2: Optional[float] = None
    CRP: Optional[float] = None
    ESR: Optional[float] = None
    Procalcitonin: Optional[float] = None
    Lactate: Optional[float] = None
    HbA1c: Optional[float] = None
    Fructosamine: Optional[float] = None

@app.post("/evaluate")
def evaluate_case(case: PatientCase):
    stage_score = case.wound_grade + case.ischemia_grade + case.infection_grade
    if stage_score <= 3:
        wIfI_stage = 1
    elif stage_score <= 5:
        wIfI_stage = 2
    elif stage_score <= 7:
        wIfI_stage = 3
    else:
        wIfI_stage = 4

    # Rule-based AI logic (with IDSA/PEDIS consideration)
    if (case.CRP or 0) > 150 and (case.Procalcitonin or 0) > 1.0 and wIfI_stage == 4:
        risk = "Critical"
        rec = "Severe infection per IDSA/PEDIS. Admit for IV antibiotics, surgical source control, and vascular consult."
    elif (case.CRP or 0) > 100 or (case.Lactate or 0) > 2.0 or (case.ESR or 0) > 70:
        risk = "High"
        rec = "Moderate-to-severe infection suspected (IDSA/PEDIS). Consider inpatient admission and imaging for possible osteomyelitis or systemic infection."
    elif wIfI_stage in [2,3]:
        risk = "Moderate"
        rec = "Likely moderate infection (PEDIS grade 2). Recommend outpatient wound care and vascular referral. Monitor labs and healing."
    else:
        risk = "Low"
        rec = "No systemic signs (IDSA grade 1 or lower). Routine care. Optimize glucose control and offloading."

    return {
        "WIfI_stage": wIfI_stage,
        "AI_risk_level": risk,
        "AI_summary": rec
    }
