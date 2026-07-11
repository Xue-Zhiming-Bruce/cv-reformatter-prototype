export const PRICING_BY_LOCALE = {
  ko: {
    currency: "₩",
    free: { price: 0, conversionsPerMonth: 5 },
    pro: { price: 19_000, originalPrice: 29_000 },
  },
  en: {
    currency: "$",
    free: { price: 0, conversionsPerMonth: 5 },
    // TODO: replace with real USD prices before launch
    pro: { price: 15, originalPrice: 22 },
  },
} as const

export type PricingLocale = keyof typeof PRICING_BY_LOCALE

export function getPricing(lng: string) {
  return PRICING_BY_LOCALE[lng as PricingLocale] ?? PRICING_BY_LOCALE.en
}
