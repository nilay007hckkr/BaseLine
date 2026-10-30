import type { ComponentType } from "react";
import { useApp, type ViewKey } from "../state";
import { SubTabs } from "../ui";
import { Docs } from "./Docs";
import { Manual } from "./Manual";
import { Overview } from "./Overview";
import { Profile } from "./Profile";
import { Risk } from "./Risk";
import { Settings } from "./Settings";

export const MORE_PAGES: { key: ViewKey; label: string; view: ComponentType }[] = [
  { key: "risk", label: "Bus factor", view: Risk },
  { key: "docs", label: "Docs", view: Docs },
  { key: "data", label: "The data", view: Overview },
  { key: "settings", label: "Settings", view: Settings },
  { key: "manual", label: "Manual", view: Manual },
  { key: "profile", label: "Profile", view: Profile },
];

/** Everything that is not one of the main questions, one sub-page at a time. */
export function More() {
  const { view, go } = useApp();
  const page = MORE_PAGES.find((p) => p.key === view) ?? MORE_PAGES[0];
  if (!page) return null;
  const View = page.view;
  return (
    <>
      <SubTabs
        tabs={MORE_PAGES.map((p) => ({ key: p.key, label: p.label }))}
        value={page.key}
        onChange={(key) => {
          go(key);
        }}
      />
      <View />
    </>
  );
}
