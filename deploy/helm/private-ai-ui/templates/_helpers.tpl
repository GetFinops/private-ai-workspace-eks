{{/*
Expand the name of the chart.
*/}}
{{- define "private-ai-ui.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "private-ai-ui.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "private-ai-ui.labels" -}}
helm.sh/chart: {{ include "private-ai-ui.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "private-ai-ui.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "private-ai-ui.selectorLabels" -}}
app.kubernetes.io/name: {{ include "private-ai-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
