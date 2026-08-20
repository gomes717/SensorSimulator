# CGMSIM – Mathematical Models Reference

Sources: [cgmsim.com](https://cgmsim.com/support/model/overview.html) and
[loopinsight1](https://github.com/hpeuscher/loopinsight1) for the equations
as implemented here; see the **References** section at the bottom for the
original peer-reviewed papers each model/sensor is from. For the narrative
"why these four models, why this numerical method, how the MCU's timestep
maps to `dt_min`" companion to this reference, see
[`../docs/MODELS.md`](../docs/MODELS.md).

---

## 1  Cambridge (Hovorka) Model

10-state glucose–insulin ODE system for T1D.

### State variables

| Symbol | Unit | Description |
|--------|------|-------------|
| Q1 | mmol | Glucose mass, accessible (plasma) compartment |
| Q2 | mmol | Glucose mass, non-accessible compartment |
| S1 | mU | Subcutaneous insulin, compartment 1 |
| S2 | mU | Subcutaneous insulin, compartment 2 |
| I | mU/l | Plasma insulin concentration |
| x1 | 1/min | Insulin action on glucose transport |
| x2 | 1/min | Insulin action on glucose disposal |
| x3 | – | Insulin action on EGP suppression |
| D1 | mmol | Meal glucose, absorption compartment 1 |
| D2 | mmol | Meal glucose, absorption compartment 2 |

### ODEs

**Glucose subsystem**

```
dQ1/dt = -F01c - x1·Q1 + k12·Q2 - FR + UG + EGP0·BW·(1 - x3)
dQ2/dt =  x1·Q1 - (k12 + x2)·Q2
```

**Insulin subsystem**

```
dS1/dt = -S1/tmaxI + IIR
dS2/dt = (S1 - S2)/tmaxI
dI/dt  = S2/(tmaxI·VI·BW) - ke·I
```

**Insulin action**

```
dx1/dt = -ka1·x1 + ka1·SIT·I
dx2/dt = -ka2·x2 + ka2·SID·I
dx3/dt = -ka3·x3 + ka3·SIE·I
```

**Meal absorption**

```
dD1/dt = -D1/tmaxG + M·AG
dD2/dt = (D1 - D2)/tmaxG
```

### Intermediate quantities

```
M     = (carbs_mg / 180.16)         [mmol/min]  meal input
IIR   = iir_U_per_h × 1000 / 60    [mU/min]    insulin infusion
G     = Q1 / (VG·BW)               [mmol/l]    plasma glucose
F01c  = F01·BW·min(G/4.5, 1)       [mmol/min]
FR    = max(0, 0.003·(G − 9))      [mmol/min]  renal glucose
UG    = D2 / tmaxG                  [mmol/min]  gut absorption
Gp    = G × 18.016                  [mg/dl]     output
```

### Default parameters

| Parameter | Default | Unit | Description |
|-----------|---------|------|-------------|
| BW | 75 | kg | Body weight |
| VG | 0.16 | l/kg | Glucose volume |
| VI | 0.12 | l/kg | Insulin volume |
| k12 | 0.066 | 1/min | Glucose inter-compartment rate |
| ka1 | 0.006 | 1/min | x1 deactivation |
| ka2 | 0.060 | 1/min | x2 deactivation |
| ka3 | 0.030 | 1/min | x3 deactivation |
| ke | 0.138 | 1/min | Insulin elimination |
| tmaxI | 55 | min | Insulin absorption time |
| tmaxG | 40 | min | Meal absorption time |
| AG | 0.8 | – | Carb bioavailability |
| SIT | 51.2e-4 | 1/min·mU/l | Insulin sensitivity (transport) |
| SID | 8.2e-4 | 1/min·mU/l | Insulin sensitivity (disposal) |
| SIE | 520e-4 | 1/mU/l | Insulin sensitivity (EGP) |
| EGP0 | 0.0161 | mmol/kg/min | Endogenous glucose production |
| F01 | 0.0097 | mmol/kg/min | Non-insulin-dependent flux |
| Gpeq | 100 | mg/dl | Target fasting glucose |

### Parameter reference

**Body scaling**
- `BW` — body weight; scales every absolute flux (glucose disposal, EGP, insulin distribution) to the individual.
- `VG`, `VI` — glucose/insulin distribution volume per kg; convert compartment *masses* (Q1, S2) into *concentrations* (G, I). Bigger volume → same mass gives a lower concentration.

**Glucose kinetics**
- `k12` — exchange rate between the accessible (plasma, Q1) and non-accessible (peripheral, Q2) glucose pools; models the delay before plasma glucose "spreads out" into tissue.
- `F01` — insulin-*independent* glucose uptake (brain, red blood cells) — a constant drain regardless of insulin.
- `EGP0` — baseline liver glucose output at zero insulin action; gets shut off as `x3` (EGP suppression) rises.

**Insulin kinetics**
- `tmaxI` — time-to-peak of subcutaneous insulin absorption; larger = slower-acting insulin, flatter peak.
- `ke` — plasma insulin elimination rate; controls how long insulin lingers after it peaks.

**Insulin sensitivity** (how hard insulin concentration `I` pushes each action state)
- `SIT` — sensitivity of glucose *transport* (x1) — moves glucose between compartments.
- `SID` — sensitivity of glucose *disposal* (x2) — peripheral uptake/utilization.
- `SIE` — sensitivity of *EGP suppression* (x3) — how much insulin shuts off the liver.
- `ka1`, `ka2`, `ka3` — how fast each of x1/x2/x3 rises and decays toward its insulin-driven target; bigger = faster-acting, but also faster-fading insulin action.

**Meal**
- `tmaxG` — time-to-peak of gut glucose absorption; larger spreads a meal out longer (e.g., higher-fat meals).
- `AG` — bioavailability; fraction of ingested carbs that actually reach the blood (0.8 = 80%).

**Reference point**
- `Gpeq` — target fasting glucose used only to compute the steady-state initial condition (and the matching basal infusion via `cambridge_basal_iir_u_per_h`).

---

## 2  UVA/Padova T1DMS Model

17-state physiological model (Visentin 2015, Dalla Man 2014).

### State variables (core, no glucagon)

| Symbol | Unit | Description |
|--------|------|-------------|
| Gp | mg/kg | Plasma glucose |
| Gt | mg/kg | Tissue glucose |
| Gs | mg/kg | Subcutaneous glucose |
| Ip | pmol/kg | Plasma insulin |
| Il | pmol/kg | Liver insulin |
| Qsto1 | mg | Stomach glucose, solid |
| Qsto2 | mg | Stomach glucose, liquid |
| Qgut | mg | Intestinal glucose |
| XL | pmol/l | Insulin delay compartment 2 |
| I_ | pmol/l | Insulin delay compartment 1 |
| X | pmol/l | Insulin in interstitial fluid |
| Isc1 | pmol/kg | Subcutaneous insulin, cpt 1 |
| Isc2 | pmol/kg | Subcutaneous insulin, cpt 2 |

### ODEs

**Glucose**

```
dGp/dt = EGP + Ra - Uii - E - k1·Gp + k2·Gt
dGt/dt = -Uid + k1·Gp - k2·Gt
dGs/dt = (Gp - Gs) / Td
```

**Meal / gut**

```
dQsto1/dt = -kgri·Qsto1 + M
dQsto2/dt = -kempt·Qsto2 + kgri·Qsto1
dQgut/dt  = -kabs·Qgut  + kempt·Qsto2
```

**Insulin**

```
dIp/dt  = -(m2 + m4)·Ip + m1·Il + Rai
dIl/dt  = m2·Ip - (m1 + m3eq)·Il
dIsc1/dt = -(kd + ka1)·Isc1 + IIR/BW
dIsc2/dt = kd·Isc1 - ka2·Isc2
```

**Insulin action**

```
dXL/dt = -ki·(XL - I_)
dI_/dt = -ki·(I_ - I)
dX/dt  = -p2u·X + p2u·(I - Ib)
```

### Algebraic equations

```
G     = Gp / VG                                 [mg/dl]
I     = Ip / VI                                  [pmol/l]
Ra    = f·kabs·Qgut / BW                        [mg/kg/min]
Rai   = ka1·Isc1 + ka2·Isc2                     [pmol/kg/min]
Uii   = Fcns                                     [mg/kg/min]
Uid   = (Vm0 + Vmx·X)·Gt / (Km0 + Gt)          [mg/kg/min]
E     = max(ke1·(Gp − ke2), 0)                  [mg/kg/min]
EGP   = max(kp1 − kp2·Gp − kp3·XL, 0)          [mg/kg/min]
m3eq  = HEeq·m1 / (1 − HEeq)
kempt = kmin + (kmax−kmin)/2·(tanh(α·(Qsto−b·D))−tanh(β·(Qsto−c·D))+2)
        α = 5/(2·D·(1−b)),  β = 5/(2·D·c),  D = max(MealMemory, 1)
IIR   = iir_U_per_min × 6000                    [pmol/min]
M     = carbs_g_per_min × 1000                  [mg/min]
```

### Default parameters (subset)

| Parameter | Default | Unit |
|-----------|---------|------|
| BW | 75 | kg |
| VG | 1.88 | dl/kg |
| VI | 0.05 | l/kg |
| k1 | 0.065 | 1/min |
| k2 | 0.079 | 1/min |
| m1 | 0.190 | 1/min |
| m2 | 0.484 | 1/min |
| m4 | 0.194 | 1/min |
| kmin | 0.0080 | 1/min |
| kmax | 0.0558 | 1/min |
| kgri | 0.0558 | 1/min |
| kabs | 0.057 | 1/min |
| ki | 0.0079 | 1/min |
| Fcns | 1.0 | mg/kg/min |
| Vm0 | 2.5 | mg/kg/min |
| Vmx | 0.047 | mg/kg/min per pmol/l |
| Km0 | 225.59 | mg/kg |
| p2u | 0.0331 | 1/min |
| kp1 | 2.7 | mg/kg/min |
| kp2 | 0.0021 | 1/min |
| kp3 | 0.009 | mg/kg/min per pmol/l |
| ke1 | 0.0005 | 1/min |
| ke2 | 339 | mg/kg |
| ka1 | 0.0018 | 1/min |
| ka2 | 0.0182 | 1/min |
| kd | 0.0164 | 1/min |
| Td | 10 | min |
| bmeal | 0.69 | – |
| cmeal | 0.17 | – |
| f | 0.90 | – |
| HEeq | 0.6 | – |
| Gpeq | 100 | mg/dl |

### Parameter reference

**Body scaling** — `BW`, `VG`, `VI` play the same role as in the Cambridge model (different units: VG in dl/kg, VI in l/kg).

**Glucose kinetics**
- `k1`, `k2` — two-way exchange rate between plasma glucose `Gp` and tissue glucose `Gt`.
- `Fcns` — insulin-independent glucose consumption (CNS + RBCs), the UVA/Padova analog of Cambridge's `F01`.
- `Vm0`, `Vmx`, `Km0` — Michaelis-Menten shape of peripheral (muscle) uptake: `Vm0` is baseline capacity, `Vmx` is how much extra capacity each unit of insulin action `X` adds, `Km0` is the tissue-glucose level at which uptake hits half its max rate.
- `kp1` — baseline hepatic glucose output (like `EGP0`).
- `kp2` — direct suppression of EGP by rising plasma glucose (glucose self-regulation of the liver).
- `kp3` — suppression of EGP by delayed insulin action `XL` — the liver's insulin sensitivity.
- `ke1`, `ke2` — renal glucose clearance: `ke1` is the rate, `ke2` is the glucose threshold above which the kidneys start spilling glucose (glucosuria safety valve).

**Insulin kinetics**
- `m1`, `m2`, `m4` — `m2`: plasma→liver insulin uptake rate; `m1`: liver→plasma return rate; `m4`: peripheral (non-hepatic) clearance. Together with `HEeq` (hepatic extraction fraction, → `m3eq`) they set total insulin clearance.
- `ka1`, `ka2`, `kd` — subcutaneous insulin PK: `kd` moves insulin cpt1→cpt2, `ka1`/`ka2` are the rates each compartment's insulin appears in plasma. Shapes how fast a bolus acts.
- `ki` — delay of insulin *action* (via `XL`, `I_`) behind plasma insulin — a hepatic/peripheral action lag.
- `p2u` — how fast insulin action `X` itself responds to plasma insulin; smaller = slower, more prolonged action.
- `Ib` — basal/reference plasma insulin [pmol/l]; `X = 0` exactly when `I = Ib`.

**Meal**
- `kgri` — stomach solid→liquid grinding rate (Qsto1→Qsto2).
- `kabs` — intestinal absorption rate (Qgut → blood).
- `kmin`, `kmax`, `bmeal`, `cmeal` — bound and shape the sigmoid gastric-emptying rate `kempt(t)`: `kmax` applies right after eating (fast emptying), `kmin` during the lag plateau; `bmeal`/`cmeal` set where the transition happens relative to how much meal remains.
- `f` — fraction of absorbed gut glucose that actually appears in plasma (splanchnic first-pass extraction).

**Sensor lag**
- `Td` — lag between plasma glucose `Gp` and the "subcutaneous" glucose `Gs` a CGM would see.

**Reference point** — `Gpeq`, same role as in the Cambridge model.

---

## 3  Roy/Parker 2007 – Exercise Model

Extends the Bergman minimal model with hepatic glucose production, exercise, and a two-compartment meal model.

### ODEs

```
dNG/dt       = Gemp − kG·NG
dPVO2max/dt  = −0.8·PVO2max + 0.8·exercise_pct
dI/dt        = −n·I + p4·IIR − Ie
dX/dt        = −p2·X + p3·(I − Ib)
dG/dt        = −p1·(G − Gpeq) − X·G + (BW/VolG)·(Gprod − Ggly − Gup) + Gemp/VolG
dGprod/dt    = a1·PVO2max − a2·Gprod
dGup/dt      = a3·PVO2max − a4·Gup
dIe/dt       = a5·PVO2max − a6·Ie
dGgly/dt     = k·PVO2max  (exercise)  |  −Ggly/T1  (recovery)
```

Meal: trapezoidal gastric emptying over [0, Tasc+Tmax+Tdes].

### Default parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| BW | 70 kg | Body weight |
| p1 | 0.035 1/min | Glucose effectiveness |
| p2 | 0.050 1/min | Insulin action dynamics |
| p3 | 2.8e-5 1/min²/(µU/ml) | Insulin sensitivity |
| p4 | 9.8e-5 | Insulin dynamics |
| n | 0.142 1/min | Insulin elimination |
| a1–a6 | see table | Exercise coefficients |
| kG | 0.022 1/min | Glucose absorption |
| VolG | 117 dl | Glucose volume |
| Tasc | 10 min | Meal ascent phase |
| Tmax | 35 min | Meal plateau |
| Tdes | 10 min | Meal descent phase |
| k | 0.0108 | Glycogenolysis increase |
| T1 | 6 min | Glycogenolysis decay |

### Parameter reference

**Glucose/insulin core**
- `p1` — glucose effectiveness: self-clearance of glucose back toward `Gpeq`, independent of insulin.
- `p2` — decay rate of remote insulin action `X` — how long insulin's effect lingers after insulin itself drops.
- `p3` — insulin sensitivity: how strongly `I` above basal `Ib` drives `X` down glucose.
- `p4` — gain converting infusion rate (IIR) into plasma insulin rise.
- `n` — plasma insulin elimination rate.
- `Ib`, `u1b` — basal plasma insulin and the basal infusion rate that sustains it at init.

**Exercise coupling** (three parallel gain/decay pairs, one per exercise-driven effect)
- `a1`/`a2` — gain/decay of exercise-boosted hepatic glucose production (`Gprod`).
- `a3`/`a4` — gain/decay of exercise-boosted glucose uptake (`Gup`).
- `a5`/`a6` — gain/decay of exercise-accelerated insulin removal (`Ie`) — models the real phenomenon where exercise speeds up insulin clearance, raising hypo risk.
- `k`, `T1` — glycogenolysis: `k` is how fast liver glycogen breakdown ramps up during exercise, `T1` is how fast that extra release fades during recovery.

**Meal**
- `kG` — absorption rate of the meal glucose pool `NG` into blood.
- `VolG` — glucose distribution volume, converts mass flux into concentration.
- `Tasc`, `Tmax`, `Tdes` — the three phases of the trapezoidal meal-absorption profile (rise, plateau, fall) — together set how long a meal takes to fully absorb.

**Body scaling** — `BW` scales hepatic glucose fluxes into the glucose ODE via `(BW/VolG)`.

---

## 4  Deichmann 2021 – Exercise-Augmented Minimal Model

Augments the Bergman model with a continuous exercise effect through heart rate.

### ODEs

```
dx1/dt   = −k21·x1 + (IIR − IIRb)
dx2/dt   = k21·x1 − (kd + ka)·x2
dIc/dt   = ka/(Vi·BW)·x2 − ke·Ic
dD1/dt   = −D1/τm + M·AG
dD2/dt   = (D1 − D2)/τm
dX/dt    = −p2·X + p3·ΔI
dG/dt    = −p1·(G − Gb) − X·G − α·HRint·Z·(X + Xb)·G − β·Y·G + Ra/(Vg·BW)
dY/dt    = (HR − HRb − Y) / tauHR
dZ/dt    = −(f + 1/τ)·Z + f
dHRint/dt = HR − HRb
```

Where:
```
ΔI  = Ic − Ib
Xb  = p3·Ib / p2
Ra  = D2/τm × AG × 1000     [mg/min → scale by 1/(Vg·BW)]
IIR = iir_U_per_h × 1000/60 [µU/min]
```

### Default parameters

| Parameter | Default | Unit |
|-----------|---------|------|
| BW | 70 | kg |
| Gpeq | 100 | mg/dl |
| Gb | 172 | mg/dl (basal glucose) |
| p1 | 0.0041 | 1/min |
| p2 | 0.0155 | 1/min |
| p3 | 6.913e-6 | 1/min²/(µU/ml) |
| alpha | 2.59e-4 | 1/bpm |
| beta | 3.39e-4 | 1/bpm |
| tauHR | 5 | min |
| tau | 600 | min |
| f | 0.1 | – |
| AG | 0.8 | – |
| Vg | 1.6 | dl/kg |
| tau_m | 60 | min |
| k21 | 0.0085 | 1/min |
| kd | 0.0247 | 1/min |
| ka | 0.011 | 1/min |
| ke | 0.0357 | 1/min |
| Vi | 104 | ml/kg |
| Ib | 10 | µU/ml |
| HRb | 80 | bpm |

### Parameter reference

**Glucose/insulin core**
- `p1` — glucose effectiveness (same role as Roy/Parker's `p1`).
- `p2` — decay rate of insulin action `X`.
- `p3` — insulin sensitivity, drives `X` from `(Ic - Ib)`.
- `Gb` vs `Gpeq` — `Gb` is the basal glucose level the ODE actually relaxes toward (172 mg/dl, from the source paper); `Gpeq` is only used to seed the steady-state initial condition (100 mg/dl) — these two are not the same number in this model.
- `Ib`, `IIRb` — basal plasma insulin and the basal infusion rate that sustains it.

**Exercise coupling** — this model's distinguishing feature is splitting exercise into an *acute* and a *sensitizing* effect:
- `beta` — acute effect: how strongly current heart-rate elevation (`Y`) increases glucose uptake right now.
- `alpha` — sensitizing effect: how strongly accumulated exercise load (`HRint`) combined with the slow recovery state `Z` amplifies insulin's action on glucose — this is what produces delayed, post-exercise hypoglycemia risk (elevated sensitivity that lingers for hours).
- `tauHR` — how fast `Y` tracks actual HR changes (short time constant, minutes).
- `tau`, `f` — govern the slow decay of the sensitizing state `Z` after exercise ends (`tau` ~ hours) and how fast `Z` builds up while exercising (`f`).
- `HRb` — resting heart rate; the neutral point below which exercise terms are inactive.

**Meal**
- `tau_m` — meal absorption time constant (Cambridge's `tmaxG` equivalent).
- `AG`, `Vg` — bioavailability and distribution volume, same role as in the other models.

**Insulin PK**
- `k21`, `kd`, `ka`, `ke`, `Vi` — subcutaneous insulin absorption chain and plasma clearance, same structural role as UVA/Padova's `ka1`/`ka2`/`kd` but with different depot topology.

**Body scaling** — `BW` scales the glucose appearance term `Ra` via `(Vg·BW)`.

---

## 5  CGMSIM Basic Models (cgmsim.com)

> These are reference formulas from cgmsim.com, kept here for documentation.
> They are **not** wired into `main.c` or any of the four `cgmsim_*.c` model
> files — the simulator only runs the four full ODE models above.

### 5.1  Insulin Sensitivity Factor (ISF)

```
ΔBG [mmol/l] = dose [U] × ISF [mmol/l/U]
CF  [mmol/l/g] = ISF / CR              (Carb Factor)
ΔBG [mmol/l]  = carbs [g] × CF
```

### 5.2  Mealtime Insulin – Biexponential Model

Parameters: `td` = DIA (min, default 300), `tp` = time to peak (min, default 60).

```
τ    = tp·(1 − tp/td) / (1 − 2·tp/td)
a    = 2·τ / td
S    = 1 / ((1−a) + (1+a)·exp(−td/τ))       (normalization constant)
Ia(t)= (S/τ²)·t·(1−t/td)·exp(−t/τ)          0 ≤ t ≤ td
```

∫₀ᵗᵈ Ia(t) dt = 1 (by construction of S).

Long-acting insulin types use the same curve with different (DIA, peak):

| Type | DIA (h) | Peak |
|------|---------|------|
| Detemir | 14 + 24·dose/weight | DIA/3 |
| Glargine U100 | 22 + 12·dose/weight | DIA/2.5 |
| Glargine U300 | 24 + 14·dose/weight | DIA/2.5 |
| Degludec | 42 (fixed) | 14 h (fixed) |

### 5.3  Carbohydrate Absorption – Bilinear Model

Peak rate: `h = 2·dose/AT`,  triangle area = dose.

```
CAR(t) = (4·dose/AT²)·t            for 0 ≤ t ≤ AT/2   (rising)
CAR(t) = (4·dose/AT)·(1 − t/AT)   for AT/2 < t ≤ AT  (falling)
```

Fast carbs (first 40 g): AT = 60 min.  
Slow carbs (remainder): AT = 240 min.

Absorbed by time t:

```
absorbed(t) = (2·dose/AT²)·t²                                     for t ≤ AT/2
absorbed(t) = dose/2 + (4·dose/AT)·((t − t²/(2·AT)) − 3·AT/8)   for t > AT/2
```

### 5.4  Endogenous Glucose Production (EGP) – Sinusoidal Model

```
modifier(t) = 1 + 0.2·cos(2π·(t_min − 360) / 1440)
```

Peak at 06:00 (dawn effect, modifier = 1.2), trough at 18:00 (modifier = 0.8).

```
EGP_rate [g/min] = 0.11 [g/kg/h] × weight_kg / 60 × modifier(t)
ΔBG [mmol/l]     = EGP_rate × CF × dt
```

---

## 6  Sensors

### 6.1  Ideal CGM

```
CGM(t) = G(t)     every simulation step
```
No noise, no lag, no internal state — every call returns the current true
value (removed 2026-08-18: an earlier version throttled output to once per
configurable `samplingTime`, default 5 min; see the Parameter reference note
below for why that was removed).

### 6.2  Ideal SMBG

```
SMBG = G(t)       on demand (point measurement)
```

### 6.3  Breton & Kovatchev 2008 – CGM Noise

Linear calibration + AR(1) noise:

```
noise(t) = PACF·noise(t−1) + σ·ε(t),    ε ~ N(0,1)
CGM(t)   = α·IG(t) + β + noise(t)
```

Default: PACF = 0.7, σ = 1.5 mg/dl, α = 1.0, β = 0.0.
Noise updated and a reading output on every simulation step (no output
throttling — see the Parameter reference note below).

### 6.4  Facchinetti et al. 2014 – Time-Varying CGM Error

Two AR(2) noise components plus time-varying calibration:

```
a(t) = a0 + a1·dt + a2·dt²          (relative error, dt = days since calibration)
b(t) = b0 + b1·dt + b2·dt²          (bias/drift)

v(t)  = α_w1·v(t−1) + α_w2·v(t−2) + w_v,   w_v ~ N(0, σ²_v)
cc(t) = α_c1·cc(t−1) + α_c2·cc(t−2) + w_c,  w_c ~ N(0, σ²_c)
noise(t) = v(t) + cc(t)

CGM(t) = a(t)·IG(t) + b(t) + noise(t)
```

Default coefficients:

| Symbol | Value | Unit |
|--------|-------|------|
| a0 | 1.1 | – |
| a1 | 2e-4 | 1/day |
| b0 | −14.8 | mg/dl |
| b1 | 0.04 | mg/dl/day |
| α_w1 | 1.013 | – |
| α_w2 | −0.2135 | – |
| σ²_v | 14.45 | (mg/dl)² |
| α_c1 | 1.23 | – |
| α_c2 | −0.3995 | – |
| σ²_c | 11.3 | (mg/dl)² |

### Parameter reference

- No sensor has a `sampling_time_min` parameter (removed 2026-08-18).
  Originally every sensor throttled its own output to once per configurable
  interval (default 5 min), mirroring real CGM hardware's sampling rate.
  That's now gone: every sensor emits a fresh reading on every simulation
  step, unconditionally. How often a *client* actually observes a new value
  is a transport/reporting-cadence concern (on the board: `comm_thread`'s
  own poll/notify interval), not something this model layer decides —
  keeping the two throttles separate was confusing in practice (BLE
  notifications arrived every few seconds regardless, since the standard
  CGMS library periodically re-notifies its last stored record, but the
  *value* only changed every 5 minutes, looking frozen in between).
- `pacf` (Breton) — noise autocorrelation coefficient; higher means smoother, more slowly-drifting noise from one reading to the next (real sensor noise isn't white — it wanders).
- `sigma` (Breton), `sigma_v`/`sigma_c` (Facchinetti) — noise magnitude; how far a single reading can stray from the true value.
- `alpha`, `beta` (Breton) — linear calibration gain/offset applied on top of the true glucose — a fixed miscalibration, not noise.
- `a0-a2`, `b0-b2` (Facchinetti) — time-varying calibration drift as the sensor ages since its last calibration (`t_calib_days`), reset by `facchinetti_recalibrate`.
- `aw1`/`aw2` and `ac1`/`ac2` (Facchinetti) — AR(2) coefficients for two separate noise components: a per-sensor measurement noise term and a shared/common error component, combined into one `noise(t)`.

---

## References

Original peer-reviewed sources for each model/sensor above (the equations
as transcribed in this file come via cgmsim.com/loopinsight1, cross-checked
against these):

1. **Cambridge (Hovorka) model** — Hovorka R, Canonico V, Chassin LJ, et al.
   "Nonlinear model predictive control of glucose concentration in subjects
   with type 1 diabetes." *Physiological Measurement*, 25(4):905–920, 2004.
   [doi:10.1088/0967-3334/25/4/010](https://iopscience.iop.org/article/10.1088/0967-3334/25/4/010)
2. **UVA/Padova T1DMS model** — Dalla Man C, Rizza RA, Cobelli C. "Meal
   simulation model of the glucose-insulin system." *IEEE Transactions on
   Biomedical Engineering*, 54(10):1740–1749, 2007.
   [PubMed 17926672](https://pubmed.ncbi.nlm.nih.gov/17926672/)
3. **Roy/Parker exercise model** — Roy A, Parker RS. "Dynamic modeling of
   exercise effects on plasma glucose and insulin levels." *Journal of
   Diabetes Science and Technology*, 1(3):338–347, 2007.
   [PubMed 19885088](https://www.ncbi.nlm.nih.gov/pubmed/19885088)
4. **Deichmann exercise-augmented minimal model** — Deichmann J, Bachmann S,
   Burckhardt M-A, Szinnai G, Kaltenbach H-M. "Simulation-Based Evaluation
   of Treatment Adjustment to Exercise in Type 1 Diabetes." *Frontiers in
   Endocrinology*, 12:723812, 2021.
   [doi:10.3389/fendo.2021.723812](https://doi.org/10.3389/fendo.2021.723812)
5. **Breton & Kovatchev CGM sensor noise** — Breton M, Kovatchev B.
   "Analysis, modeling, and simulation of the accuracy of continuous glucose
   sensors." *Journal of Diabetes Science and Technology*, 2(5):853–862,
   2008. [PMC2740661](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC2740661/)
6. **Facchinetti et al. time-varying CGM error** — Facchinetti A, Del Favero
   S, Sparacino G, Cobelli C. "Modeling the glucose sensor error." *IEEE
   Transactions on Biomedical Engineering*, 61(3):620–629, 2014.
   [doi:10.1109/TBME.2013.2284023](https://pubmed.ncbi.nlm.nih.gov/24108706/)
