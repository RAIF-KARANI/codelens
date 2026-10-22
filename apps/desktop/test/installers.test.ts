import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { download, INSTALLERS, organisation, type Progress, signatureProblem } from "../src/installers.js";

describe("organisation", () => {
  it("reads the O= of a certificate subject, as Windows prints it", () => {
    // Copied from Get-AuthenticodeSignature on this machine (step 5a).
    const docker =
      "CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US, SERIALNUMBER=4817464, OID.2.5.4.15=Private Organization";
    const ollama = "CN=Ollama Inc., O=Ollama Inc., L=Toronto, S=Ontario, C=CA, SERIALNUMBER=2713355";
    expect(organisation(docker)).toBe("Docker Inc");
    expect(organisation(ollama)).toBe("Ollama Inc.");
    expect(organisation('CN=x, O="Comma, Inc.", C=US')).toBe("Comma, Inc.");
    expect(organisation("CN=no organisation")).toBeNull();
  });

  it("isn't fooled by an O= inside another field", () => {
    expect(organisation("CN=Docker Inc, OU=O=Docker Inc, O=Evil Ltd")).toBe("Evil Ltd");
  });

  it("isn't fooled by an O= inside a quoted value (security audit run-2)", () => {
    // How Windows formats a subject whose CN or L holds a comma.
    expect(organisation('CN="Evil, O=Docker Inc, Ltd", O=Evil Corp, C=US')).toBe("Evil Corp");
    expect(organisation('CN=Evil Corp, L="x, O=Ollama Inc., y", O=Evil Corp, C=US')).toBe("Evil Corp");
    expect(organisation('CN=x, O="Say ""hi"", Inc.", C=US')).toBe('Say "hi", Inc.');
  });

  it("has no publisher when the O is missing or repeated", () => {
    expect(organisation("CN=Docker Inc, O=Docker Inc, O=Evil Ltd")).toBeNull();
    expect(organisation("CN=Docker Inc + O=Docker Inc, O=Evil Ltd")).toBeNull();
  });
});

describe("signatureProblem", () => {
  const docker = INSTALLERS.docker.publisher;

  it("accepts a valid signature by the publisher", () => {
    expect(signatureProblem({ status: "Valid", subject: "CN=Docker Inc, O=Docker Inc" }, docker)).toBeNull();
  });

  it.each([
    [null, "couldn't be read"],
    [{ status: "NotSigned", subject: "" }, "isn't valid (NotSigned)"],
    [{ status: "HashMismatch", subject: "CN=Docker Inc, O=Docker Inc" }, "isn't valid (HashMismatch)"],
    [{ status: "Valid", subject: "CN=Docker Inc, O=Docker Inc Ltd" }, "signed by Docker Inc Ltd, not Docker Inc"],
    [{ status: "Valid", subject: "CN=Docker Inc" }, "signed by an unknown publisher"],
  ])("refuses %j", (signature, reason) => {
    expect(signatureProblem(signature, docker)).toContain(reason);
  });
});

describe("download", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "codelens-dl-"));
  });
  afterEach(async () => rm(dir, { recursive: true, force: true }));

  const body = Buffer.from("installer bytes ".repeat(1000));
  const fake = (status: number, bytes: Buffer, length = bytes.length) =>
    (async () =>
      new Response(bytes, { status, headers: { "content-length": String(length) } })) as unknown as typeof fetch;

  it("writes the whole file under its real name, reporting progress", async () => {
    const seen: Progress[] = [];
    const path = await download(INSTALLERS.ollama, dir, (p) => seen.push(p), fake(200, body));
    expect(path).toBe(join(dir, "OllamaSetup.exe"));
    expect(await readFile(path)).toEqual(body);
    expect(seen.at(-1)).toEqual({ received: body.length, total: body.length });
    expect(await readdir(dir)).toEqual(["OllamaSetup.exe"]);
  });

  it("keeps nothing from a refused or short download", async () => {
    await expect(download(INSTALLERS.ollama, dir, () => {}, fake(503, Buffer.alloc(0)))).rejects.toThrow(/HTTP 503/);
    await expect(
      download(INSTALLERS.ollama, dir, () => {}, fake(200, body.subarray(0, 100), body.length)),
    ).rejects.toThrow(/stopped early/);
    expect(await readdir(dir)).toEqual([]);
  });
});
