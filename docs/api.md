# API reference

Everything documented here is re-exported from the top-level `archivey` package and
listed in `archivey.__all__`. Narrative guide: [Home](index.md). Authoritative
contracts: `openspec/specs/`.

## Opening archives

::: archivey.open_archive
::: archivey.open_stream
::: archivey.extract
::: archivey.detect_format
::: archivey.format_availability
::: archivey.list_supported_formats
::: archivey.list_known_formats

## The reader interface

::: archivey.ArchiveReader
::: archivey.ArchiveStream
::: archivey.MemberSelector
::: archivey.MemberStreams

## Data model

::: archivey.ArchiveMember
::: archivey.ArchiveInfo
::: archivey.ArchiveFormat
::: archivey.ContainerFormat
::: archivey.StreamFormat
::: archivey.MemberType
::: archivey.HashAlgorithm
::: archivey.crc32_digest
::: archivey.CompressionAlgorithm
::: archivey.CompressionMethod
::: archivey.CreateSystem

## Diagnostics

Structured advisories (formerly log-only warnings). See the `diagnostics` capability
spec for lifecycle, retention, and policy.

::: archivey.Diagnostic
::: archivey.DiagnosticCode
::: archivey.DiagnosticSeverity
::: archivey.DiagnosticDisposition
::: archivey.DiagnosticPolicy
::: archivey.DiagnosticSummary
::: archivey.OnDiagnostic
::: archivey.ExtractionReport

## Extraction

::: archivey.ExtractionResult
::: archivey.ExtractionStatus
::: archivey.ExtractionPolicy
::: archivey.OverwritePolicy
::: archivey.OnError
::: archivey.MemberFilter

## Configuration

::: archivey.ArchiveyConfig
::: archivey.ExtractionLimits
::: archivey.ListingLimits
::: archivey.AcceleratorMode
::: archivey.PasswordInput
::: archivey.PasswordRequest
::: archivey.PasswordProvider

## Access cost

::: archivey.CostReceipt
::: archivey.ListingCost
::: archivey.AccessCost
::: archivey.StreamCapability

## Measurement

::: archivey.IoStats
::: archivey.enable_measurement

## Errors

::: archivey.ArchiveyError
::: archivey.ResourceLimitError
::: archivey.DiagnosticRaisedError
::: archivey.ArchiveyUsageError
