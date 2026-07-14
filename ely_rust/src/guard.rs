/// Simple prompt injection guard — detects common patterns.
pub fn sanitize(input: &str) -> (&str, bool) {
    let flags = ["ignore previous instructions", "ignore all instructions",
                 "system:", "<|im_start|>", "<|im_end|>", "DAN ", "STAN "];
    let lower = input.to_lowercase();
    for flag in &flags {
        if lower.contains(flag) {
            return (input, true);
        }
    }
    (input, false)
}
