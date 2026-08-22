---
title: "{{title}}"
description: "{{description}}"
tags: ["reference", "api", "{{service}}"]
---

# {{title}}

{{description}}

## Base URL

```
{{base_url}}
```

## Authentication

{{auth_method}} — see [Authentication Guide](../guides/auth.md)

## Endpoints

### {{endpoint_group}}

#### `{{METHOD}} {{path}}`

{{endpoint_description}}

**Path Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `{{param_1}}` | `{{type_1}}` | {{required_1}} | {{param_1_desc}} |

**Query Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `{{query_1}}` | `{{type_1}}` | {{required_1}} | `{{default_1}}` | {{query_1_desc}} |

**Request Body**

```{{language}}
{{request_example}}
```

**Response**

```{{language}}
{{response_example}}
```

**Status Codes**

| Code | Description |
|------|-------------|
| `200` | {{success_desc}} |
| `400` | {{error_400_desc}} |
| `401` | {{error_401_desc}} |
| `404` | {{error_404_desc}} |
| `429` | {{error_429_desc}} |

**Example**

```bash
curl -X {{METHOD}} "{{base_url}}{{path}}" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{{request_json}}'
```

## Error Responses

All errors follow this format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": {}
  }
}
```

| Error Code | HTTP Status | Cause |
|------------|-------------|-------|
| `{{error_code_1}}` | `{{status_1}}` | {{cause_1}} |
| `{{error_code_2}}` | `{{status_2}}` | {{cause_2}} |

## Rate Limits

{{rate_limit_description}}

| Tier | Requests | Window |
|------|----------|--------|
| {{tier_1}} | {{limit_1}} | {{window_1}} |
| {{tier_2}} | {{limit_2}} | {{window_2}} |

## Pagination

{{pagination_description}}

```
{{pagination_example}}
```

## Versioning

{{versioning_strategy}}

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| `{{version}}` | `{{date}}` | {{changes}} |

## Related

- [{{related_endpoint}}]({{related_link}})
- [{{related_guide}}]({{guide_link}})
- [SDK Reference](../reference/sdk.md)