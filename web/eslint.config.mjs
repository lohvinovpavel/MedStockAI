import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

// Matches what `next lint`'s first-run wizard would have generated under
// "Strict (recommended)" — written by hand instead of run interactively,
// since that wizard can't answer its own prompt in CI.
const eslintConfig = [...compat.extends("next/core-web-vitals", "next/typescript")];

export default eslintConfig;
