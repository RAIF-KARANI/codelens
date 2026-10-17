/** Drives the desktop stack from a terminal, for development and for checking
 * a change before the app's window exists (step 1b):
 *
 *   pnpm stack init              create the data folder and its secrets
 *   pnpm stack up [--local]      start everything, wait until healthy, print the URL
 *   pnpm stack status [--local]  each service's state
 *   pnpm stack stop [--local]    stop it (the data stays)
 *   pnpm stack env [--local]     the variables compose.yaml wants, for running it by hand
 *
 * --local builds CodeLens's images from this checkout (compose.build.yaml)
 * instead of using the published ones pinned in images.json. */

import {
  codelensHome,
  composeEnv,
  ensureHome,
  LOCAL_IMAGES,
  newLaunch,
  readImages,
  type Stack,
  status,
  stop,
  up,
} from "./stack.js";

async function stackFrom(args: string[]): Promise<Stack> {
  const local = args.includes("--local");
  return {
    home: codelensHome(),
    images: local ? LOCAL_IMAGES : await readImages(),
    build: local,
  };
}

const write = (chunk: string) => process.stdout.write(chunk);

async function main(command: string | undefined, args: string[]): Promise<number> {
  switch (command) {
    case "init": {
      const home = codelensHome();
      const { envFile, created } = await ensureHome(home);
      console.log(`data folder: ${home}`);
      console.log(created.length ? `generated in ${envFile}: ${created.join(", ")}` : `${envFile}: already set up`);
      return 0;
    }
    case "up": {
      const stack = await stackFrom(args);
      await ensureHome(stack.home);
      const launch = await newLaunch();
      const started = performance.now();
      const run = await up(stack, launch, write);
      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      if (run.code !== 0) {
        console.error(`\ndocker compose up failed (exit ${run.code}) after ${seconds} s`);
        return run.code;
      }
      console.log(`\nrunning and healthy after ${seconds} s`);
      console.log(`dashboard: http://127.0.0.1:${launch.port}`);
      // Development only: the app's window holds this key itself. Printed so a
      // change can be checked with curl before that window exists.
      console.log(`this launch's key (dev only): ${launch.appKey}`);
      console.log(
        `  curl -H "Cookie: codelens_app_key=${launch.appKey}" http://127.0.0.1:${launch.port}/api/health`,
      );
      return 0;
    }
    case "stop": {
      const run = await stop(await stackFrom(args), write);
      return run.code;
    }
    case "status": {
      const run = await status(await stackFrom(args));
      write(run.output);
      return run.code;
    }
    case "env": {
      for (const [name, value] of Object.entries(composeEnv(await stackFrom(args)))) {
        console.log(`${name}=${value}`);
      }
      return 0;
    }
    default:
      console.error("usage: pnpm stack init | up | status | stop | env  [--local]");
      return 2;
  }
}

main(process.argv[2], process.argv.slice(3)).then(
  (code) => process.exit(code),
  (error: unknown) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  },
);
