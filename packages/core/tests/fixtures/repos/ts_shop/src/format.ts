/** Formats an amount as dollars. */
export default function formatPrice(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

export const round2 = (n: number) => Math.round(n * 100) / 100;
