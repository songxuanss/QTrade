import { expect } from "@playwright/test";

/** Exercise the same visible navigation as a person, without a direct hidden
 * element click. Keeps domain acceptance scripts independent of page stacking. */
export async function openDataView(page, primary, secondary) {
  await page
    .getByRole("tablist", { name: "数据管理分区", exact: true })
    .getByRole("tab", { name: primary, exact: true })
    .click();
  if (secondary)
    await page.getByRole("tab", { name: secondary, exact: true }).click();
  await expect(
    page
      .getByRole("tablist", { name: "数据管理分区", exact: true })
      .getByRole("tab", { name: primary, exact: true }),
  ).toHaveAttribute("aria-selected", "true");
}
