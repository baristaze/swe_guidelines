// The shared ESLint base every TypeScript package extends. A package's own
// eslint.config.js spreads `base` and adds only what is specific to it.
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export const base = [js.configs.recommended, ...tseslint.configs.recommended];

export default tseslint.config(...base);
