import { createCn } from "cn/config";

/**
 * Class merging that knows Sakhi's type scale. Without this, `text-meta` and friends
 * look like colours and get dropped whenever a colour class is merged in.
 */
export const cn = createCn({
  extend: { classGroups: { "font-size": [{ text: ["meta", "body", "lead", "title", "display", "hero"] }] } },
});
