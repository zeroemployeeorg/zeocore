import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { Ajv2020 } from "ajv/dist/2020.js";
import standalone from "ajv/dist/standalone/index.js";
import { build } from "esbuild";
const root = fileURLToPath(new URL("..", import.meta.url));
for (const [contract, out] of [
  ["contract.ts", "shape.js"],
  ["calendar-contract.ts", "calendar-shape.js"],
]) {
  const source = await readFile(root + "/src/generated/" + contract, "utf8");
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
    outfile: root + "/src/generated/" + out,
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2022",
  });
}
