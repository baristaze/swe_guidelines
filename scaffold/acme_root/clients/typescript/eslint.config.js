import tseslint from "typescript-eslint";
import { base } from "../../eslint.config.js";

// The package is the one place that calls fetch and reads the generated
// schema, so it takes the base alone; the apps forbid both.
export default tseslint.config({ ignores: ["src/schema.d.ts"] }, ...base);
