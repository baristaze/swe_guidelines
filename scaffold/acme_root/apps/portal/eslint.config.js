import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";
import { base } from "../../eslint.config.js";

export default tseslint.config(...base, {
  files: ["src/**/*.{ts,tsx}"],
  plugins: { "react-hooks": reactHooks },
  rules: {
    ...reactHooks.configs.recommended.rules,
    // The app never calls fetch and never reads the generated schema: it
    // imports the client package, clients/typescript/, by its name alone.
    "no-restricted-globals": ["error", { name: "fetch", message: "use the client, @acme/client" }],
    "no-restricted-imports": [
      "error",
      {
        patterns: [
          { group: ["@acme/client/*", "**/schema", "**/schema.d"], message: "import the facade, @acme/client" },
        ],
      },
    ],
  },
});
