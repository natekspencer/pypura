#!/bin/bash

YEAR=$(date +"%Y")

# Works with: "Copyright (c) 2023", "Copyright © 2022–2024", etc.
sed -i '' -E "s/(Copyright( \(c\)| ©)? [0-9]{4})(–[0-9]{4})?/\1–$YEAR/" LICENSE
