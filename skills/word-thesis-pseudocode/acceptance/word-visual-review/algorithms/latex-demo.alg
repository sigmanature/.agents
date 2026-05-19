@algorithm id=latex-demo chapter=auto index=auto title=Latex Alias Demo

\Input inode, folio
\Output dispatch result
\For each candidate in dispatch_table
    \If match(candidate, folio)
        \Return candidate.handler
    \Else
        scan next candidate
    \EndIf
\EndFor
\Return default_handler