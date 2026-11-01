import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { Ajv2020 } from "ajv/dist/2020.js";
import standalone from "ajv/dist/standalone/index.js";
import { build } from "esbuild";
const root = fileURLToPath(new URL("..", import.meta.url));
const source = await readFile(root + "/src/generated/contract.ts", "utf8");
const data = JSON.parse(
  source
    .slice(source.indexOf("export const contract = ") + 24)
    .trim()
    .replace(/;$/, ""),
);
const ajv = new Ajv2020({
  strict: false,
  validateFormats: false,
  code: { source: true, esm: true },
});
const validate = ajv.compile(data.schema);
await build({
  stdin: {
    contents: standalone(ajv, validate),
    resolveDir: root,
    loader: "js",
  },
  outfile: root + "/src/generated/shape.js",
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2022",
});
