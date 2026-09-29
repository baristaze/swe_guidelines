import tseslint from "typescript-eslint";
import { base } from "../../eslint.config.js";

export default tseslint.config({ ignores: ["dist"] }, ...base);
