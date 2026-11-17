import type research from '../data/research.json'

export type ResearchCase = (typeof research.cases)[number]
export type ResearchStudy = (typeof research.studies)[number]
