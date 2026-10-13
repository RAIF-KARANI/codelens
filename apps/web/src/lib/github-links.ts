/** Links to a repo's pages on github.com. */
const GITHUB = "https://github.com";

export function githubUrl(fullName: string): string {
  return `${GITHUB}/${fullName}`;
}

export function pullRequestUrl(fullName: string, number: number): string {
  return `${GITHUB}/${fullName}/pull/${number}`;
}

export function commitUrl(fullName: string, sha: string): string {
  return `${GITHUB}/${fullName}/commit/${sha}`;
}
