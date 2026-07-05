{{- define "model-installer.name" -}}model-installer{{- end -}}
{{- define "model-installer.labels" -}}
app.kubernetes.io/name: model-installer
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
