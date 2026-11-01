// node --test browser/tests/  (no browser needed)
// The model answers with word pieces and labels but no positions; entitaeten() finds them in
// the text. If this goes wrong, a name could slip past the redaction, so it is tested on its own.
import { test } from "node:test";
import assert from "node:assert/strict";
import { entitaeten, stuecke } from "../helper-worker.js";

const tok = (index, word, entity) => ({ index, word, entity, score: 0.99 });

test("word pieces become one name with character positions", () => {
  const text = "Bei Frau Kowalczyk in Lüneburg 3 Std.";
  const tokens = [
    tok(1, "Bei", "O"), tok(2, "Frau", "O"), tok(3, "Kow", "B-PER"), tok(4, "##al", "I-PER"),
    tok(5, "##czyk", "I-PER"), tok(6, "in", "O"), tok(7, "Lü", "B-LOC"), tok(8, "##neburg", "I-LOC"),
    tok(9, "3", "O"), tok(10, "Std", "O"), tok(11, ".", "O"),
  ];
  const found = entitaeten(text, tokens);
  assert.deepEqual(found.map((e) => [e.label, text.slice(e.start, e.end)]), [["PER", "Kowalczyk"], ["LOC", "Lüneburg"]]);
});

test("a name cut inside a word is widened to the whole word", () => {
  const text = "Rechnung an Bergers Garten";
  const tokens = [tok(1, "Rechnung", "O"), tok(2, "an", "O"), tok(3, "Berg", "B-PER"), tok(4, "##ers", "O"), tok(5, "Garten", "O")];
  const [e] = entitaeten(text, tokens);
  assert.equal(text.slice(e.start, e.end), "Bergers");
});

test("two people next to each other stay two names", () => {
  const text = "Anna Ben";
  const tokens = [tok(1, "Anna", "B-PER"), tok(2, "Ben", "B-PER")];
  assert.equal(entitaeten(text, tokens).length, 2);
});

test("the same word earlier in the text doesn't confuse the positions", () => {
  const text = "Berger Weg 1: Frau Berger";
  const tokens = [tok(1, "Berger", "O"), tok(2, "Weg", "O"), tok(3, "1", "O"), tok(4, ":", "O"), tok(5, "Frau", "O"), tok(6, "Berger", "B-PER")];
  const [e] = entitaeten(text, tokens);
  assert.equal(e.start, text.lastIndexOf("Berger"));
});

test("offset moves positions into the whole text; chunks cover everything", () => {
  const text = ("Zeile mit Inhalt\n".repeat(120)) + "Ende";
  const teile = stuecke(text);
  assert.equal(teile.map(([, t]) => t).join(""), text);
  for (const [offset, t] of teile) {
    assert.equal(text.slice(offset, offset + t.length), t);
    assert.ok(t.length <= 800);
  }
  const [e] = entitaeten("Frau Kurz", [tok(1, "Frau", "O"), tok(2, "Kurz", "B-PER")], 100);
  assert.deepEqual([e.start, e.end], [105, 109]);
});
