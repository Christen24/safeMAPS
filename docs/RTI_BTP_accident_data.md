# RTI Application — Bangalore City Police Accident Data

**Status:** Ready to File  
**Statutory deadline for response:** 30 days from filing under RTI Act 2005  
**Authority:** Commissioner of Police, Bengaluru City  
**Target data:** Historical accident records with GPS coordinates, 2022–2024

---

## Draft RTI Letter

```
To: The Public Information Officer,
    Office of the Commissioner of Police,
    Bengaluru City,
    M.S. Building, Dr. Ambedkar Veedhi,
    Bangalore – 560 001.

Subject: Application Under Section 6(1) of the Right to Information Act, 2005
         — Request for Historical Road Accident Data (2022–2024)

Applicant: [Your Full Name]
Address:   [Your Address]
Phone:     [Your Phone]
Email:     [Your Email]

Date: [Filing Date]

Respected PIO,

Under the provisions of Section 6(1) of the Right to Information Act, 2005,
I hereby request the following information:

1. A list of all road accident incidents reported to Bangalore City Police
   during the period January 1, 2022 to December 31, 2024 (or the most
   recent available period), containing the following fields per incident:
   
   a) Date and time of accident
   b) GPS coordinates (Latitude and Longitude) of the accident location
   c) Road name or area description (if GPS not available)
   d) Nature of accident (fatal / grievous / minor)
   e) Number of persons injured/killed (without personal identification)
   f) Road type (national highway / state highway / city road etc.)
   g) Cause of accident (if recorded)

2. Alternatively, if GPS coordinates are not available in the database:
   Please provide the list of accident locations by junction name,
   road name, or police station jurisdiction for the same period.

3. The total count of fatal accidents per police station jurisdiction
   in Bangalore for the years 2022, 2023, and 2024.

Note: I am requesting aggregate/anonymised location data only.
No personal information about accident victims or drivers is requested.

Purpose: This data is being used for a non-commercial public interest
research project to develop a safety-aware routing system for urban areas.
The project (SafeMAPS) is open source.

I am enclosing the RTI fee of ₹10 via Indian Postal Order / Court Fee Stamp.

Yours faithfully,
[Your Signature]
[Your Name]
[Date]
```

---

## What to Do With the Data When Received

1. Save the file as `data_pipeline/data/btp_accidents_2022_2024.csv`
2. Run the importer:
   ```bash
   cd data_pipeline
   python btp_accident_importer.py --file data/btp_accidents_2022_2024.csv --clear
   ```
3. Reload the routing graph:
   ```bash
   curl -X POST http://localhost:8000/api/admin/refresh-graph \
        -H "X-Admin-Key: $ADMIN_API_KEY"
   ```
4. The graph cache will rebuild with real accident risk scores replacing
   the synthetic blackspots currently in the database.

---

## Filing Options

| Method | Link | Fee |
|--------|------|-----|
| Online (India) | https://rtionline.gov.in | ₹10 online |
| Karnataka RTI Portal | https://rtikpsc.karnataka.gov.in | ₹10 |
| Physical (BCP Office) | M.S. Building, Bangalore | ₹10 IPO |

**Note:** BTP (Bangalore Traffic Police) is under Bangalore City Police.
File to: *Commissioner of Police, Bengaluru City* (not Karnataka Police).
