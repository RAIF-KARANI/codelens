import { Tabs } from "@/components/tabs";

export function RepoTabs({ repoId }: { repoId: number }) {
  return (
    <Tabs
      tabs={[
        { href: `/repos/${repoId}`, label: "Reviews" },
        { href: `/repos/${repoId}/indexing`, label: "Indexing" },
      ]}
    />
  );
}
