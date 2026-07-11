# API reference

Everything documented here is re-exported from the top-level `archivey` package and
listed in `archivey.__all__`.

## Opening archives

::: archivey.open_archive
::: archivey.extract
::: archivey.detect_format

## The reader interface

::: archivey.ArchiveReader
::: archivey.ArchiveStream

## Data model

::: archivey.ArchiveMember
::: archivey.ArchiveInfo
::: archivey.ArchiveFormat
::: archivey.ContainerFormat
::: archivey.StreamFormat
::: archivey.MemberType
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
::: archivey.ExtractionReport

## Extraction

::: archivey.ExtractionResult
::: archivey.ExtractionStatus
::: archivey.ExtractionPolicy
::: archivey.OverwritePolicy
::: archivey.OnError

## Configuration

::: archivey.ArchiveyConfig
::: archivey.ExtractionLimits
::: archivey.AcceleratorMode

## Access cost

::: archivey.CostReceipt
::: archivey.ListingCost
::: archivey.AccessCost
::: archivey.StreamCapability

## Errors

::: archivey.ArchiveyError
::: archivey.DiagnosticRaisedError
