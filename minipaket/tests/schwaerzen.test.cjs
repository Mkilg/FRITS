// node --test minipaket/tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../schwaerzen.js");

const BRIEF = [
  "Sehr geehrte Frau Albrecht,",
  "wie besprochen schicke ich Ihnen die Nebenkostenabrechnung 2025 für Ihre Wohnung im Gartenweg 12, 50667 Köln.",
  "Die Nachzahlung beträgt 214,60 €. Bitte überweisen Sie bis 30.11.2026 auf DE89 3704 0044 0532 0130 00.",
  "Rückfragen gern an hausverwaltung.koenig@example.de oder 0221 9988776.",
  "Mit freundlichen Grüßen",
  "Petra König, Hausverwaltung König",
  "Aktenzeichen: NK-2025-0815",
].join("\n");

function werte(r) {
  return Object.keys(r.zuordnung).map((p) => r.zuordnung[p].wert);
}

test("der Beispielbrief: alles Persönliche weg, Beträge und Fristen bleiben", () => {
  const r = S.schwaerzen(BRIEF);
  for (const geheim of ["Albrecht", "Gartenweg 12", "50667 Köln", "DE89 3704 0044 0532 0130 00",
    "hausverwaltung.koenig@example.de", "0221 9988776", "Petra König", "NK-2025-0815"]) {
    assert.ok(werte(r).includes(geheim), geheim);
    assert.ok(!r.geschwaerzt.includes(geheim), geheim);
  }
  for (const bleibt of ["214,60 €", "30.11.2026", "Nebenkostenabrechnung 2025"]) {
    assert.ok(r.geschwaerzt.includes(bleibt), bleibt);
  }
  // Der Nachname allein in "Hausverwaltung König" ist der typische Rest: als Hinweis gemeldet
  assert.deepEqual(r.hinweise, ["König"]);
});

test("ein Hinweis-Name schwärzt den Rest, ohne den ganzen Namen zu zerteilen", () => {
  const r = S.schwaerzen(BRIEF, { namen: ["König"] });
  assert.ok(!/König/.test(r.geschwaerzt));
  assert.ok(werte(r).includes("Petra König"));
  assert.deepEqual(r.hinweise, []);
});

test("gleicher Wert, gleicher Platzhalter", () => {
  const r = S.schwaerzen("Herr Meier ruft an. Später noch einmal Herr Meier.");
  assert.equal(r.geschwaerzt, "Herr [PERSON_1] ruft an. Später noch einmal Herr [PERSON_1].");
});

test("Muster: Steuer-ID mit Prüfziffer, Steuernummer, USt-IdNr., Geburtsdatum, Telefon", () => {
  const text = "Steuer-ID 86 095 742 719, geb. 03.04.1985, Tel. +49 151 23456789, " +
    "Steuernummer 21/815/08150, USt-IdNr. DE123456789";
  const r = S.schwaerzen(text);
  assert.equal(r.geschwaerzt,
    "Steuer-ID [STEUERID_1], geb. [GEBDATUM_1], Tel. [TEL_1], Steuernummer [STEUERNR_1], USt-IdNr. [USTID_1]");
  assert.ok(S._intern.steuerIdGueltig("86095742719"));
  assert.ok(!S._intern.steuerIdGueltig("86095742718"));
});

test("eine IBAN mit falscher Prüfsumme bleibt, eine richtige geht", () => {
  assert.ok(S._intern.ibanGueltig("DE89370400440532013000"));
  assert.ok(!S._intern.ibanGueltig("DE89370400440532013001"));
  assert.match(S.schwaerzen("IBAN DE89 3704 0044 0532 0130 00").geschwaerzt, /^IBAN \[IBAN_1\]$/);
});

test("Anrede, Gruß und Vornamen", () => {
  const r = S.schwaerzen("Hallo Jonas,\ndanke dir!\nViele Grüße\nAnna Lena Schmidt\n\nLiebe Grüße\nDein Team");
  assert.equal(r.geschwaerzt, "Hallo [PERSON_1],\ndanke dir!\nViele Grüße\n[PERSON_2]\n\nLiebe Grüße\nDein Team");
  assert.equal(S.schwaerzen("Liebe Frau Albrecht,").geschwaerzt, "Liebe Frau [PERSON_1],");
  assert.equal(S.schwaerzen("Herr Dr. Müller-Lüdenscheidt").geschwaerzt, "Herr Dr. [PERSON_1]");
});

test("Adressen und Orte, aber keine Beträge oder Stunden", () => {
  const r = S.schwaerzen("Am Markt 3, 80331 München. 12345 Euro für 40 Stunden.");
  assert.equal(r.geschwaerzt, "[ADRESSE_1], [ORT_1]. 12345 Euro für 40 Stunden.");
});

test("eigene Wörter, auch im Genitiv; ein s bleibt stehen", () => {
  const r = S.schwaerzen("Malerbetrieb Huber und Hubers Leiter", { woerter: "Huber\nMalerbetrieb" });
  assert.equal(r.geschwaerzt, "[GEHEIM_1] [GEHEIM_2] und [GEHEIM_2]s Leiter");
});

test("Aufgedecktes bleibt stehen", () => {
  const r = S.schwaerzen("Herr Meier, 50667 Köln", { aufdecken: ["ORT:50667 köln"] });
  assert.equal(r.geschwaerzt, "Herr [PERSON_1], 50667 Köln");
});

test("Platzhalter im Text bleiben unberührt", () => {
  const r = S.schwaerzen("Grüße an [PERSON_1] und Herrn Meier");
  // Die Nummer 1 ist schon vergeben, sonst würde das Zurücksetzen beide verwechseln
  assert.equal(r.geschwaerzt, "Grüße an [PERSON_1] und Herrn [PERSON_2]");
});

test("zurücksetzen verzeiht kleine Abweichungen der KI", () => {
  const r = S.schwaerzen(BRIEF);
  const antwort = "Liebe [PERSON_1], die Summe geht auf [IBAN_1]. Gruß, Person_2. Nicht: PERSONAL_1, [PERSON_9]";
  assert.equal(S.zuruecksetzen(antwort, r.zuordnung),
    "Liebe Albrecht, die Summe geht auf DE89 3704 0044 0532 0130 00. Gruß, Petra König. Nicht: PERSONAL_1, [PERSON_9]");
});

test("der fertige Prompt enthält nur Geschwärztes und die Aufgabe", () => {
  const r = S.schwaerzen(BRIEF);
  const p = S.prompt(r.geschwaerzt, "Ist die Nachzahlung plausibel?");
  assert.ok(p.includes("Aufgabe: Ist die Nachzahlung plausibel?"));
  assert.ok(p.includes("[PERSON_2]"));
  assert.ok(!p.includes("Albrecht"));
  assert.ok(S.prompt("x").includes("HIER DEINE AUFGABE"));
});

test("die Vornamen-Liste hat keine Doppelten und keine leeren Einträge", () => {
  const v = S._intern.VORNAMEN;
  assert.equal(new Set(v).size, v.length);
  assert.ok(v.every((n) => /^[A-ZÄÖÜ][a-zäöüßé]+$/.test(n)), v.filter((n) => !/^[A-ZÄÖÜ][a-zäöüßé]+$/.test(n)).join());
});
