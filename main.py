from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

app = FastAPI(title="LIMBLinkAI Backend v1.1")

# CORS so HTML/Bubble can call your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten later to your domains
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Models ----------
class PatientCase(BaseModel):
    # WIfI (as entered by clinician)
    wound_grade: int
    ischemia_grade: int
    infection_grade: int

    # Perfusion (optional: used to refine ischemia grade)
    ABI: Optional[float] = None
    Toe_pressure: Optional[float] = None
    TcPO2: Optional[float] = None

    # Biomarkers
    CRP: Optional[float] = None
    ESR: Optional[float] = None
    Procalcitonin: Optional[float] = None
    Lactate: Optional[float] = None

    # Metabolic
    HbA1c: Optional[float] = None
    Fructosamine: Optional[float] = None


# ---------- Utility logic ----------
def safe(v, default=None):
    return v if v is not None else default

def refine_ischemia_grade(
    user_grade: int,
    abi: Optional[float],
    toe: Optional[float],
    tcpo2: Optional[float],
    notes: List[str]
) -> int:
    """
    Refine ischemia grade using Toe pressure and TcPO2 when ABI is noncompressible (>1.30)
    or missing; otherwise respect the worst of (user_grade, derived_from_toe/tcpo2).
    """
    derived = None

    # Derive grade from Toe pressure (mmHg) if present
    if toe is not None:
        if toe < 30:
            derived = max(3, derived or 3)
            notes.append(f"Toe pressure {toe} mmHg → Ischemia 3")
        elif 30 <= toe <= 39:
            derived = max(2, derived or 2)
            notes.append(f"Toe pressure {toe} mmHg → Ischemia 2")
        elif toe >= 40:
            derived = max(0, derived or 0)
            notes.append(f"Toe pressure {toe} mmHg → Ischemia 0–1")

    # Derive grade from TcPO2 (mmHg) if present
    if tcpo2 is not None:
        if tcpo2 < 20:
            derived = max(3, derived or 3)
            notes.append(f"TcPO₂ {tcpo2} mmHg → Ischemia 3")
        elif 20 <= tcpo2 <= 29:
            derived = max(2, derived or 2)
            notes.append(f"TcPO₂ {tcpo2} mmHg → Ischemia 2")
        elif tcpo2 >= 30:
            derived = max(0, derived or 0)
            notes.append(f"TcPO₂ {tcpo2} mmHg → Ischemia 0–1")

    # If ABI is clearly noncompressible, prefer microcirculatory metrics
    if abi is not None and abi > 1.30:
        notes.append(f"ABI {abi} (>1.30): likely noncompressible → prefer toe/TcPO₂")
        return derived if derived is not None else user_grade

    # Otherwise, respect worst case between user grade and derived
    if derived is None:
        return user_grade
    return max(user_grade, derived)


def wifI_stage_estimate(w: int, i: int, f: int, notes: List[str]) -> int:
    """
    Practical stage estimator.
    NOTE: True SVS WIfI uses a published matrix; this approximation escalates stage with higher grades.
    Replace with matrix lookup when you’re ready to lock it to the table.

    Heuristic:
      - Any 3 with another ≥2 → Stage 4
      - Sum ≥7 → Stage 3–4 (map to 4 if max grade=3, else 3)
      - Sum 4–6 → Stage 2–3 (map to 3 if any grade=3, else 2)
      - Sum ≤3 → Stage 1
    """
    mx = max(w, i, f)
    s = w + i + f
    if (w == 3 and (i >= 2 or f >= 2)) or (i == 3 and (w >= 2 or f >= 2)) or (f == 3 and (w >= 2 or i >= 2)):
        notes.append("High component grades (≥3 with another ≥2) → Stage 4")
        return 4
    if s >= 7:
        stage = 4 if mx == 3 else 3
        notes.append(f"Sum {s} with max {mx} → Stage {stage}")
        return stage
    if 4 <= s <= 6:
        stage = 3 if mx == 3 else 2
        notes.append(f"Sum {s} with max {mx} → Stage {stage}")
        return stage
    notes.append(f"Sum {s} → Stage 1")
    return 1


def idsa_pedis_with_biomarkers(
    crp: Optional[float],
    esr: Optional[float],
    pct: Optional[float],
    lact: Optional[float],
    notes: List[str]
) -> Dict[str, Any]:
    """
    Minimal IDSA/PEDIS style mapping with biomarker up/down grading.
    """
    # Defaults
    grade = "Low"       # akin to IDSA mild/none
    level = "Low"

    # Critical/systemic triggers
    if (lact is not None and lact >= 4.0) or (pct is not None and pct >= 2.0):
        grade = "Severe"
        level = "Critical"
        if lact is not None and lact >= 4.0:
            notes.append(f"Lactate {lact} ≥4.0 → Severe infection")
        if pct is not None and pct >= 2.0:
            notes.append(f"Procalcitonin {pct} ≥2.0 → Severe infection")
        return {"idsa_pedis_grade": grade, "risk_level": level}

    # High (moderate-severe risk)
    high_flags = []
    if crp is not None and crp >= 100:
        high_flags.append(f"CRP {crp} ≥100")
    if esr is not None and esr >= 70:
        high_flags.append(f"ESR {esr} ≥70")
    if lact is not None and 2.0 <= lact < 4.0:
        high_flags.append(f"Lactate {lact} 2.0–3.9")
    if pct is not None and 0.5 <= pct < 2.0:
        high_flags.append(f"PCT {pct} 0.5–1.99")

    if high_flags:
        for x in high_flags:
            notes.append(x + " → Moderate–Severe infection")
        return {"idsa_pedis_grade": "Moderate", "risk_level": "High"}

    # Moderate (mild–moderate)
    mod_flags = []
    if crp is not None and 50 <= crp < 100:
        mod_flags.append(f"CRP {crp} 50–99")
    if mod_flags:
        for x in mod_flags:
            notes.append(x + " → Mild–Moderate infection")
        return {"idsa_pedis_grade": "Mild–Moderate", "risk_level": "Moderate"}

    # Otherwise low
    notes.append("No systemic biomarker red flags → Low infection severity")
    return {"idsa_pedis_grade": "None–Mild", "risk_level": "Low"}


def plan_recommendations(
    stage: int,
    inf_level: str,
    refined_I: int,
    hbA1c: Optional[float],
    flags: List[str]
) -> List[str]:
    recs = []

    # Infection-driven actions
    if inf_level == "Critical":
        recs += [
            "Admit now; start IV broad-spectrum antibiotics",
            "Urgent surgical source control if abscess/necrosis",
        ]
    elif inf_level == "High":
        recs += [
            "Strongly consider admission for IV antibiotics",
            "Imaging for osteomyelitis (MRI or bone biopsy if indicated)",
        ]
    elif inf_level == "Moderate":
        recs += [
            "Outpatient management reasonable; close follow-up 24–48h",
            "Consider targeted antibiotics per cultures, debridement if needed",
        ]
    else:
        recs += ["Routine wound care and offloading; monitor"]

    # Ischemia-driven actions
    if refined_I == 3 or stage >= 3:
        recs.append("Vascular consult within 24–48 hours")
    elif refined_I == 2:
        recs.append("Vascular referral; noninvasive testing as needed")

    # Glycemic/optimization
    if hbA1c is not None and hbA1c >= 9.0:
        flags.append(f"HbA1c {hbA1c} ≥9% → slower healing expected")
        recs.append("Intensify glycemic control and nutrition optimization")

    return recs


# ---------- Endpoints ----------

@app.post("/evaluate")
def evaluate_legacy(case: PatientCase):
    """
    Legacy endpoint kept for compatibility. Uses improved infection logic but returns the simple fields.
    """
    notes: List[str] = []

    # Refine ischemia via perfusion metrics
    refined_I = refine_ischemia_grade(
        case.ischemia_grade, case.ABI, case.Toe_pressure, case.TcPO2, notes
    )

    # Estimate stage (heuristic; replace with matrix when ready)
    stage = wifI_stage_estimate(case.wound_grade, refined_I, case.infection_grade, notes)

    # Infection risk summary
    inf = idsa_pedis_with_biomarkers(case.CRP, case.ESR, case.Procalcitonin, case.Lactate, notes)

    # Map to overall AI risk level (prefer infection risk, escalate with stage)
    level = inf["risk_level"]
    if level in ["Low", "Moderate"] and stage >= 3:
        level = "High"

    # Simple summary text
    if level == "Critical":
        summary = "Severe infection/systemic risk. Admit for IV antibiotics, urgent source control, and vascular consult."
    elif level == "High":
        summary = "Moderate–severe infection likely. Consider admission, imaging for osteomyelitis, and vascular evaluation."
    elif level == "Moderate":
        summary = "Mild–moderate infection. Outpatient care reasonable with close follow-up; consider targeted antibiotics."
    else:
        summary = "Low infection severity. Routine care, offloading, and monitoring."

    return {
        "WIfI_stage": stage,
        "AI_risk_level": level,
        "AI_summary": summary,
    }


@app.post("/evaluate_v2")
def evaluate_v2(case: PatientCase):
    """
    Detailed evaluation returning rationale, refined ischemia, and plan.
    """
    rationale: List[str] = []

    # 1) Refine ischemia grade
    refined_I = refine_ischemia_grade(
        case.ischemia_grade, case.ABI, case.Toe_pressure, case.TcPO2, rationale
    )

    # 2) Stage estimate (swap to published matrix later)
    stage = wifI_stage_estimate(case.wound_grade, refined_I, case.infection_grade, rationale)

    # 3) Infection grading with biomarkers
    inf = idsa_pedis_with_biomarkers(case.CRP, case.ESR, case.Procalcitonin, case.Lactate, rationale)
    inf_level = inf["risk_level"]

    # 4) Compose recommendations
    plan_flags: List[str] = []
    recs = plan_recommendations(stage, inf_level, refined_I, case.HbA1c, plan_flags)

    # 5) Overall risk (conservative merge)
    overall = inf_level
    if overall in ["Low", "Moderate"] and stage >= 3:
        overall = "High"
        rationale.append(f"WIfI Stage {stage} escalates overall risk → High")

    return {
        "wound": {"W": case.wound_grade, "I": refined_I, "F": case.infection_grade, "wIfI_stage": stage},
        "perfusion": {
            "ABI": safe(case.ABI),
            "Toe_pressure": safe(case.Toe_pressure),
            "TcPO2": safe(case.TcPO2),
        },
        "infection": {
            "CRP": safe(case.CRP),
            "ESR": safe(case.ESR),
            "Procalcitonin": safe(case.Procalcitonin),
            "Lactate": safe(case.Lactate),
            "idsa_pedis_grade": inf["idsa_pedis_grade"],
            "risk_level": inf_level,
        },
        "metabolic": {"HbA1c": safe(case.HbA1c), "Fructosamine": safe(case.Fructosamine)},
        "plan": {"recommendations": recs, "flags": plan_flags},
        "rationale": rationale,
        "version": "1.1.0",
    }
