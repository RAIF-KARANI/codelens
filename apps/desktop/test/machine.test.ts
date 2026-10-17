import { describe, expect, it } from "vitest";

import {
  checkDisk,
  checkGpu,
  checkMemory,
  checkVirtualization,
  checkWindows,
  parseNvidiaSmi,
} from "../src/machine.js";

const GiB = 1024 ** 3;

describe("the machine's checks", () => {
  it("Windows: 10 22H2 or 11, x64", () => {
    expect(checkWindows("10.0.26300", "x64")).toMatchObject({ status: "ok", detail: "Windows 11 (build 26300)" });
    expect(checkWindows("10.0.19045", "x64")).toMatchObject({ status: "ok", detail: "Windows 10 (build 19045)" });
    expect(checkWindows("10.0.19044", "x64").status).toBe("fail");
    expect(checkWindows("10.0.26300", "arm64").status).toBe("fail");
  });

  it("virtualization: on in firmware, or a hypervisor already running", () => {
    // This machine: WSL2 running, so the firmware flag reads false.
    expect(checkVirtualization({ hypervisor: true, firmware: false }).status).toBe("ok");
    expect(checkVirtualization({ hypervisor: false, firmware: true }).status).toBe("ok");
    expect(checkVirtualization({ hypervisor: false, firmware: false }).status).toBe("fail");
    expect(checkVirtualization(null).status).toBe("warn");
  });

  it("memory and disk", () => {
    expect(checkMemory(15.6 * GiB).status).toBe("ok");
    expect(checkMemory(8 * GiB).status).toBe("warn");
    expect(checkMemory(4 * GiB).status).toBe("fail");
    expect(checkDisk(174 * GiB).status).toBe("ok");
    expect(checkDisk(20 * GiB).status).toBe("warn");
    expect(checkDisk(10 * GiB).status).toBe("fail");
  });

  it("GPU: nvidia-smi's lines; none is a warning, not a failure", () => {
    const gpus = parseNvidiaSmi("NVIDIA GeForce RTX 4060 Laptop GPU, 8188\r\n");
    expect(gpus).toEqual([{ name: "NVIDIA GeForce RTX 4060 Laptop GPU", mib: 8188 }]);
    expect(checkGpu(gpus)).toMatchObject({ status: "ok", detail: "NVIDIA GeForce RTX 4060 Laptop GPU, 8 GB" });
    expect(checkGpu(parseNvidiaSmi("GTX 1650, 4096")).status).toBe("warn");
    expect(checkGpu(parseNvidiaSmi("")).status).toBe("warn");
    expect(parseNvidiaSmi("NVIDIA-SMI has failed because it couldn't communicate")).toEqual([]);
  });
});
