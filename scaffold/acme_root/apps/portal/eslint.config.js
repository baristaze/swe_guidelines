import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";
import { base } from "../../eslint.config.js";

export default tseslint.config(
  { ignores: ["src/api/schema.d.ts"] },
  ...base,
  {
    // Feature code: everything but src/api/, which is the one place that
    // reads the generated schema and calls fetch.
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/**"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Feature code never calls fetch; everything goes through src/api/.
      "no-restricted-globals": ["error", { name: "fetch", message: "use the client in src/api/" }],
      "no-restricted-imports": [
        "error",
        { patterns: [{ group: ["**/schema", "**/schema.d"], message: "import the facade from src/api/" }] },
      ],
    },
  },
);
