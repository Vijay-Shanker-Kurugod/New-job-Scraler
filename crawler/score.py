"""Automatic pre-score (0-100) for every new job, mirroring the profile rubric.

It only ranks; the daily scan still reads the top jobs and scores them by hand. Terms are the
candidate's verified skills (power electronics, e-drives, controls, MBD, MiL/SiL/HiL, CANoe).

  technical skills 30 · tools 18 · industry 12 · level 15 · education 7 · language 10 · contract 8
"""
import re

X = lambda p: re.compile(p, re.I)
TECH = [  # (pattern, points) – capped at 30
    (X(r"leistungselektronik|power electronic"), 8), (X(r"wechselrichter|inverter|umrichter|converter|dc[/-]dc"), 7),
    (X(r"e-?antrieb|e-?drive|electric drive|elektrische antrieb|traktion|traction"), 7),
    (X(r"e-?motor|elektromotor|elektrische maschine|electric machine|pmsm|permanent ?magnet"), 6),
    (X(r"regelungstechnik|control engineer|regelung|motor control|vector control|feldorientiert|\bfoc\b|"
       r"field[- ]weakening|mtpa|\bpll\b"), 6),
    (X(r"model[- ]based|modellbasiert|\bmbd\b|\bmil\b|\bsil\b|\bhil\b|hardware[- ]in[- ]the[- ]loop"), 6),
    (X(r"on-?board charger|\bobc\b|ladetechnik|charging|ladesystem|v2g|bidirektional"), 5),
    (X(r"funktionsentwicklung|function development|thermal|derating|simulation"), 3),
    (X(r"\bbms\b|batterie|battery|hochvolt|high[- ]voltage"), 3),
]
TOOLS = [(X(r"simulink"), 6), (X(r"\bmatlab\b"), 4), (X(r"simscape"), 3), (X(r"stateflow"), 2),
         (X(r"canoe|capl|vector"), 3), (X(r"\bpython\b"), 2), (X(r"motor-?cad|ansys|\bfem\b"), 2),
         (X(r"\bcan\b|can-bus"), 1)]
INDUSTRY = X(r"automotive|fahrzeug|vehicle|e-?mobil|elektromobil|\bev\b|xev|nutzfahrzeug|truck|antriebsstrang|"
             r"powertrain|ladeinfrastruktur|energie|energy|bahn|rail")
JUNIOR = X(r"junior|absolvent|graduate|berufseinsteiger|einsteiger|trainee|young professional|"
           r"erste berufserfahrung|first experience|0[-–]2 jahre|bis zu 2 jahre")
MID = X(r"(3|drei)\+? ?(jahre|years)|mehrjährige")
SENIOR = X(r"(5|6|7|8|10|fünf|sechs|zehn)\+? ?(jahre|years)|langjährige|senior|expert")
EDU = X(r"master|m\.? ?sc|diplom|elektrotechnik|electrical engineering|mechatron|maschinenbau|"
        r"mechanical engineering|fahrzeugtechnik|vehicle engineering")
GERMAN_HIGH = X(r"verhandlungssicher|fließend\w* deutsch|deutsch\w* fließend|sehr gute deutschkenntnisse|"
                r"exzellente deutschkenntnisse|fluent german|german \(c1|c1|c2|muttersprach")
ENGLISH_DOC = X(r"\b(the|and|you|your|we are|requirements|responsibilities)\b")
AGENCY = X(r"arbeitnehmerüberlassung|personaldienstleist|zeitarbeit|für unseren kunden|for our client|"
           r"im auftrag unseres|brunel|ferchau|hays|akkodis|orizon|randstad|adecco|gulp|amadeus fire")
HARD_STOP = X(r"sicherheitsüberprüfung|security clearance|deutsche staatsangehörigkeit|german citizenship|"
              r"\bü2\b|\bü3\b")


def prescore(j):
    title, desc = j.get("jobTitle") or "", j.get("jobDescription") or ""
    text = f"{title} \n {desc}"
    hits = []
    tech = 0
    for rx, pts in TECH:
        m = rx.search(text)
        if m:
            tech += pts; hits.append(m.group(0))
    tools = 0
    for rx, pts in TOOLS:
        m = rx.search(text)
        if m:
            tools += pts; hits.append(m.group(0))
    s = min(tech, 30) + min(tools, 18)
    s += 12 if INDUSTRY.search(text) else 4
    if JUNIOR.search(text):
        s += 15
    elif SENIOR.search(text):
        s += 3
    elif MID.search(text):
        s += 8
    else:
        s += 11                     # level not stated
    s += 7 if EDU.search(text) else 3
    flags = []
    if GERMAN_HIGH.search(text):
        s += 3; flags.append("strong German")
    else:
        s += 10
    if AGENCY.search(text + " " + (j.get("companyName") or "")):
        s += 4; flags.append("Agency")
    else:
        s += 8
    if HARD_STOP.search(text):
        s = 0; flags.append("HARD STOP: clearance/citizenship")
    if not desc:
        flags.append("title only")
    lang = "en" if len(ENGLISH_DOC.findall(text)) >= 5 else "de"
    return {"preScore": max(0, min(100, s)), "preMatched": sorted(set(h.lower() for h in hits))[:12],
            "preFlags": flags, "postingLanguage": lang}
