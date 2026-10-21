       IDENTIFICATION DIVISION.
       PROGRAM-ID. OVERFLOW-TEST.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 A PIC 9(3).
       01 B PIC 9(3).
       01 C PIC 9(3).
       01 D PIC 9(3).
       PROCEDURE DIVISION.
           ADD A TO B GIVING C.
           ADD A TO B GIVING D
               ON SIZE ERROR
                   DISPLAY "SIZE ERROR DETECTED"
               NOT ON SIZE ERROR
                   DISPLAY "NO SIZE ERROR"
           END-ADD.
           DISPLAY C.
           DISPLAY D.
           STOP RUN.
