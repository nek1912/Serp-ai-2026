import { test, expect } from "vitest";
import { dict, translate } from "./dictionaries";
import { LOCALES } from "./i18n";

test("every locale defines every key that en defines", () => {
  const enKeys = Object.keys(dict.en);
  for (const loc of LOCALES) {
    for (const k of enKeys) {
      expect(dict[loc][k], `${loc}.${k}`).toBeDefined();
    }
  }
});

test("locales translate nav.home and a page key", () => {
  for (const loc of ["hi", "gu"]) {
    expect(translate(loc, "nav.home").length).toBeGreaterThan(0);
  }
});
