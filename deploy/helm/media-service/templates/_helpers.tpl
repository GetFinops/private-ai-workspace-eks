{{- define "media-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "media-service.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "media-service.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "media-service.labels" -}}
app.kubernetes.io/name: {{ include "media-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "media-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "media-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "media-service.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- include "media-service.fullname" . -}}
{{- else -}}
default
{{- end -}}
{{- end -}}
