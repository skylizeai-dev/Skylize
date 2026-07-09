import { describe, expect, it } from "vitest";

import { DEPARTMENTS, departmentColor } from "@/components/console/department-color";

describe("departmentColor", () => {
  it("returns the palette color for every known department", () => {
    for (const dept of DEPARTMENTS) {
      expect(departmentColor(dept.id)).toBe(dept.color);
    }
  });

  it("falls back to the border token for unknown ids", () => {
    expect(departmentColor("not_a_department")).toBe("var(--color-border-strong)");
  });
});
