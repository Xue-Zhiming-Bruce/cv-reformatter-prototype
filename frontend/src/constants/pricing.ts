export const PRICING_BY_LOCALE = {
  ko: {
    currency: "₩",
    free: { price: 0 },
    personal: { price: 19_000 },
    team: { price: 99_000 },
  },
  en: {
    currency: "$",
    free: { price: 0 },
    personal: { price: 15 },
    team: { price: 79 },
  },
} as const

export type PricingLocale = keyof typeof PRICING_BY_LOCALE

export function getPricing(lng: string) {
  return PRICING_BY_LOCALE[lng as PricingLocale] ?? PRICING_BY_LOCALE.en
}
