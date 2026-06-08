# Hardened Fuzzy Extractor Prototype

This build upgrades the fuzzy extractor helper from plain SimHash + repetition coding to:

1. **Salted random feature projection**
   - Token and geometry features are projected through public-salt-derived random hyperplanes.
   - This reduces direct bias from common human drawing features.
   - It does **not** create entropy; it only makes the sketch representation less naively biased.

2. **BCH error correction when `bchlib` is installed**
   - The hidden 128-bit secret is encoded with BCH ECC when available.
   - Public helper data is `helper = sketch XOR codeword`.
   - Recovery reconstructs the codeword from a later redraw sketch and BCH-corrects it.

3. **Repetition fallback**
   - If `bchlib` is missing, the system falls back to the earlier repetition code.
   - The helper JSON reports `ecc_mode` and `ecc_warning` so experiments can distinguish modes.

## Install hardened optional dependency

```bash
pip install -r requirements-hardened.txt
```

## Important limitation

This is still a research prototype, not production cryptography. Public helper data leaks information about the sketch/codeword relation and needs formal analysis before real use.

## Why your sister failed fuzzy recovery

That is a useful signal. The normal verifier may accept/reject based on token/geometry thresholds, while fuzzy recovery additionally requires the redraw to land in the same salted sketch decoding basin. A genuine user's redraw may land close enough, while another person's imitation may look visually similar but differ in feature/sketch bits and fail the commitment check.
