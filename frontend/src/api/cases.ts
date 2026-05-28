/* ─── Case API functions ─── */

import { api } from './client';
import type {
  ApproveRequest,
  AuditTrailResponse,
  CaseListResponse,
  CaseResponse,
  CreateCaseRequest,
  HealthResponse,
  RejectRequest,
} from './types';

export const casesApi = {
  list: () => api.get<CaseListResponse>('/cases'),

  create: (data: CreateCaseRequest) => api.post<CaseResponse>('/cases', data),

  get: (caseId: string) => api.get<CaseResponse>(`/cases/${caseId}`),

  getAudit: (caseId: string) =>
    api.get<AuditTrailResponse>(`/cases/${caseId}/audit`),

  approve: (caseId: string, data: ApproveRequest) =>
    api.post<CaseResponse>(`/cases/${caseId}/approve`, data),

  reject: (caseId: string, data: RejectRequest) =>
    api.post<CaseResponse>(`/cases/${caseId}/reject`, data),

  health: () => api.get<HealthResponse>('/health'),
};
