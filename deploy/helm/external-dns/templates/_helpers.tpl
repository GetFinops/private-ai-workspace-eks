{{- define "external-dns.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "external-dns.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "external-dns.serviceAccountName" -}}
{{- default "external-dns" .Values.serviceAccount.name -}}
{{- end -}}

{{- define "external-dns.labels" -}}
app.kubernetes.io/name: {{ include "external-dns.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "external-dns.selectorLabels" -}}
app.kubernetes.io/name: {{ include "external-dns.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
