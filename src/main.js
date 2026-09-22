import {
    writeFile,
    readFile,
    access,
} from 'node:fs/promises';

import {
    execFile,
} from 'node:child_process';

import {
    promisify,
} from 'node:util';

import {
    fileURLToPath,
} from 'node:url';

import { Actor, log } from 'apify';
import { PlaywrightCrawler } from 'crawlee';

await Actor.init();

const execFileAsync =
    promisify(execFile);

/*
 * ---------------------------------------------------------
 * CONSTANTS
 * ---------------------------------------------------------
 */

const REVEL_TIME_WORKED_URL =
    'https://laynes.revelup.com/schedules/timeworked/';

const REVEL_PAYROLL_URL =
    'https://laynes.revelup.com/schedules/payroll/';

const CSV_STORE_ID =
    '42Y1TWSBcTF4EH709';

/*
 * Resolve process_reports.py relative to main.js.
 *
 * Expected structure:
 *
 * src/
 *   main.js
 *   process_reports.py
 */
const PROCESS_REPORTS_PATH =
    fileURLToPath(
        new URL(
            './process_reports.py',
            import.meta.url,
        ),
    );

const GET_SHIFT_SUMMARY_DATE_PATH =
    fileURLToPath(
        new URL(
            './get_shift_summary_date.py',
            import.meta.url,
        ),
    );


/*
 * ---------------------------------------------------------
 * HELPERS
 * ---------------------------------------------------------
 */

async function saveScreenshot(page, key) {
    const screenshot =
        await page.screenshot({
            fullPage: true,
        });

    await Actor.setValue(
        key,
        screenshot,
        {
            contentType:
                'image/png',
        },
    );

    log.info(
        `Saved screenshot: ${key}`,
    );
}


function escapeRegExp(value) {
    return String(value).replace(
        /[.*+?^${}()|[\]\\]/g,
        '\\$&',
    );
}


/*
 * Supports:
 *
 * YYYY-MM-DD
 * MM/DD/YYYY
 */
function parseInputDate(value) {
    if (!value) {
        throw new Error(
            'A date is required.',
        );
    }

    const trimmedValue =
        String(value).trim();

    let year;
    let month;
    let day;

    const isoMatch =
        trimmedValue.match(
            /^(\d{4})-(\d{2})-(\d{2})$/,
        );

    const revelMatch =
        trimmedValue.match(
            /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/,
        );

    if (isoMatch) {
        [, year, month, day] =
            isoMatch;
    } else if (revelMatch) {
        [, month, day, year] =
            revelMatch;
    } else {
        throw new Error(
            'date must use YYYY-MM-DD '
            + 'or MM/DD/YYYY format. '
            + `Received: ${value}`,
        );
    }

    const numericYear =
        Number(year);

    const numericMonth =
        Number(month);

    const numericDay =
        Number(day);

    const parsedDate =
        new Date(
            Date.UTC(
                numericYear,
                numericMonth - 1,
                numericDay,
            ),
        );

    if (
        parsedDate.getUTCFullYear()
            !== numericYear
        || parsedDate.getUTCMonth()
            !== numericMonth - 1
        || parsedDate.getUTCDate()
            !== numericDay
    ) {
        throw new Error(
            `Invalid calendar date: ${value}`,
        );
    }

    return parsedDate;
}

/*
 * ---------------------------------------------------------
 * GET SUMMARY SHIFT DATE FROM PYTHON
 * ---------------------------------------------------------
 */

async function getSummaryShiftDateFromPython() {
    /*
     * Verify the Python script exists.
     */
    try {
        await access(
            GET_SHIFT_SUMMARY_DATE_PATH,
        );

        log.info(
            'Summary shift date script found: '
            + `${GET_SHIFT_SUMMARY_DATE_PATH}`,
        );
    } catch {
        throw new Error(
            'get_shift_summary_date.py was not found at '
            + `${GET_SHIFT_SUMMARY_DATE_PATH}. `
            + 'Make sure get_shift_summary_date.py exists '
            + 'in the same src directory as main.js '
            + 'and is copied into the Docker image.',
        );
    }

    log.info(
        'Running get_shift_summary_date.py...',
    );

    let pythonResult;

    try {
        pythonResult =
            await execFileAsync(
                'python3',
                [
                    GET_SHIFT_SUMMARY_DATE_PATH,
                ],
                {
                    timeout:
                        30_000,

                    maxBuffer:
                        1024 * 1024,
                },
            );
    } catch (error) {
        const stdout =
            error?.stdout
                ? String(error.stdout).trim()
                : '';

        const stderr =
            error?.stderr
                ? String(error.stderr).trim()
                : '';

        if (stdout) {
            log.error(
                'get_shift_summary_date.py '
                + `stdout before failure: ${stdout}`,
            );
        }

        if (stderr) {
            log.error(
                'get_shift_summary_date.py '
                + `stderr: ${stderr}`,
            );
        }

        throw new Error(
            'get_shift_summary_date.py failed. '
            + `${error.message}`,
        );
    }

    const stdout =
        String(
            pythonResult.stdout ?? '',
        ).trim();

    const stderr =
        String(
            pythonResult.stderr ?? '',
        ).trim();

    if (stderr) {
        log.warning(
            'get_shift_summary_date.py '
            + `stderr: ${stderr}`,
        );
    }

    if (!stdout) {
        throw new Error(
            'get_shift_summary_date.py returned no output. '
            + 'The script must print the results '
            + 'dictionary as JSON.',
        );
    }

    let results;

    try {
        results =
            JSON.parse(
                stdout,
            );
    } catch {
        throw new Error(
            'Could not parse '
            + 'get_shift_summary_date.py output '
            + 'as JSON. '
            + `Received: ${stdout}`,
        );
    }

    const revelDateFieldShift =
        results?.revel_date_field_shift;

    if (!revelDateFieldShift) {
        throw new Error(
            'get_shift_summary_date.py output '
            + 'did not contain '
            + '"revel_date_field_shift".',
        );
    }

    /*
     * Validate the date returned from Python.
     *
     * Expected format:
     * YYYY-MM-DD
     */
    parseInputDate(
        revelDateFieldShift,
    );

    log.info(
        'Date returned by '
        + 'get_shift_summary_date.py: '
        + `${revelDateFieldShift}`,
    );

    return String(
        revelDateFieldShift,
    ).trim();
}


function formatRevelDate(date) {
    const month =
        String(
            date.getUTCMonth() + 1,
        ).padStart(2, '0');

    const day =
        String(
            date.getUTCDate(),
        ).padStart(2, '0');

    const year =
        date.getUTCFullYear();

    return `${month}/${day}/${year}`;
}


function getWeekRange(inputDate) {
    const selectedDate =
        parseInputDate(inputDate);

    const dayOfWeek =
        selectedDate.getUTCDay();

    const daysSinceMonday =
        (dayOfWeek + 6) % 7;

    const monday =
        new Date(selectedDate);

    monday.setUTCDate(
        selectedDate.getUTCDate()
        - daysSinceMonday,
    );

    const sunday =
        new Date(monday);

    sunday.setUTCDate(
        monday.getUTCDate() + 6,
    );

    return {
        rangeFrom:
            formatRevelDate(monday),

        rangeTo:
            formatRevelDate(sunday),
    };
}


function formatDateForFilename(value) {
    const [
        month,
        day,
        year,
    ] = value.split('/');

    return (
        `${year}`
        + `${month.padStart(2, '0')}`
        + `${day.padStart(2, '0')}`
    );
}


function formatEstablishmentForKey(value) {
    return String(value)
        .trim()
        .toUpperCase()
        .replace(
            /[^A-Z0-9]+/g,
            '_',
        )
        .replace(
            /^_+|_+$/g,
            '',
        );
}


/*
 * ---------------------------------------------------------
 * BUILD TIME WORKED REPORT URL
 * ---------------------------------------------------------
 */

function buildTimeWorkedReportUrl(
    rangeFrom,
    rangeTo,
) {
    const reportFilters = {
        time_format: '0',
        range_from: rangeFrom,
        range_to: rangeTo,
        day_start_offset: '00:00',
        all_roles: 'true',
        all_departments: 'false',
        show_all: '1',
        remove_empty_items: '1',
        include_total_wages: '1',
        display_roles: 'on',
        type: 'shift',
    };

    return (
        `${REVEL_TIME_WORKED_URL}`
        + `#${JSON.stringify(
            reportFilters,
        )}`
    );
}


/*
 * ---------------------------------------------------------
 * BUILD PAYROLL REPORT URL
 * ---------------------------------------------------------
 */

function buildPayrollReportUrl(
    rangeFrom,
    rangeTo,
) {
    const reportFilters = {
        role: 'all-roles',
        time_format: '0',
        range_from: rangeFrom,
        range_to: rangeTo,
        day_start_offset: '00:00',
        all_roles: '0',
        all_departments: '0',
        show_all: '1',
        remove_empty_items: '1',
        expand_all: '1',
    };

    return (
        `${REVEL_PAYROLL_URL}`
        + `#${JSON.stringify(
            reportFilters,
        )}`
    );
}


/*
 * ---------------------------------------------------------
 * FIND TIME WORKED CSV
 * ---------------------------------------------------------
 */

async function waitForTimeWorkedCsvHref(
    page,
    reportType,
    rangeFrom,
    rangeTo,
) {
    log.info(
        `Waiting for ${reportType} CSV link `
        + `for ${rangeFrom} through ${rangeTo}.`,
    );

    await page.waitForFunction(
        ({
            expectedType,
            expectedRangeFrom,
            expectedRangeTo,
        }) => {
            const links = [
                ...document.querySelectorAll(
                    'a[href^="csv/"]',
                ),
            ];

            return links.some(
                (link) => {
                    const text =
                        (
                            link.textContent
                            ?? ''
                        ).trim();

                    if (
                        text.toUpperCase()
                        !== 'CSV'
                    ) {
                        return false;
                    }

                    const href =
                        link.getAttribute(
                            'href',
                        );

                    if (!href) {
                        return false;
                    }

                    try {
                        const csvUrl =
                            new URL(
                                href,
                                window.location.href,
                            );

                        return (
                            csvUrl.searchParams
                                .get('type')
                            === expectedType
                            &&
                            csvUrl.searchParams
                                .get('range_from')
                            === expectedRangeFrom
                            &&
                            csvUrl.searchParams
                                .get('range_to')
                            === expectedRangeTo
                        );
                    } catch {
                        return false;
                    }
                },
            );
        },
        {
            expectedType:
                reportType,

            expectedRangeFrom:
                rangeFrom,

            expectedRangeTo:
                rangeTo,
        },
        {
            timeout:
                60_000,

            polling:
                500,
        },
    );

    const matchingCsvHref =
        await page.evaluate(
            ({
                expectedType,
                expectedRangeFrom,
                expectedRangeTo,
            }) => {
                const links = [
                    ...document.querySelectorAll(
                        'a[href^="csv/"]',
                    ),
                ];

                for (const link of links) {
                    const text =
                        (
                            link.textContent
                            ?? ''
                        ).trim();

                    if (
                        text.toUpperCase()
                        !== 'CSV'
                    ) {
                        continue;
                    }

                    const href =
                        link.getAttribute(
                            'href',
                        );

                    if (!href) {
                        continue;
                    }

                    try {
                        const csvUrl =
                            new URL(
                                href,
                                window.location.href,
                            );

                        if (
                            csvUrl.searchParams
                                .get('type')
                                === expectedType
                            &&
                            csvUrl.searchParams
                                .get('range_from')
                                === expectedRangeFrom
                            &&
                            csvUrl.searchParams
                                .get('range_to')
                                === expectedRangeTo
                        ) {
                            return href;
                        }
                    } catch {
                        // Ignore malformed href.
                    }
                }

                return null;
            },
            {
                expectedType:
                    reportType,

                expectedRangeFrom:
                    rangeFrom,

                expectedRangeTo:
                    rangeTo,
            },
        );

    if (!matchingCsvHref) {
        throw new Error(
            `Could not find ${reportType} CSV `
            + `matching ${rangeFrom} through `
            + `${rangeTo}.`,
        );
    }

    return new URL(
        matchingCsvHref,
        page.url(),
    );
}


/*
 * ---------------------------------------------------------
 * FIND PAYROLL CSV
 * ---------------------------------------------------------
 */

async function waitForPayrollCsvHref(
    page,
    rangeFrom,
    rangeTo,
) {
    log.info(
        'Waiting for Payroll CSV link '
        + `for ${rangeFrom} through ${rangeTo}.`,
    );

    await page.waitForFunction(
        ({
            expectedRangeFrom,
            expectedRangeTo,
        }) => {
            const links = [
                ...document.querySelectorAll(
                    'a[href^="csv/"]',
                ),
            ];

            return links.some(
                (link) => {
                    const text =
                        (
                            link.textContent
                            ?? ''
                        ).trim();

                    if (
                        text.toUpperCase()
                        !== 'CSV'
                    ) {
                        return false;
                    }

                    const href =
                        link.getAttribute(
                            'href',
                        );

                    if (!href) {
                        return false;
                    }

                    try {
                        const csvUrl =
                            new URL(
                                href,
                                window.location.href,
                            );

                        return (
                            csvUrl.searchParams
                                .get('range_from')
                            === expectedRangeFrom
                            &&
                            csvUrl.searchParams
                                .get('range_to')
                            === expectedRangeTo
                            &&
                            csvUrl.searchParams
                                .get('expand_all')
                            === '1'
                        );
                    } catch {
                        return false;
                    }
                },
            );
        },
        {
            expectedRangeFrom:
                rangeFrom,

            expectedRangeTo:
                rangeTo,
        },
        {
            timeout:
                60_000,

            polling:
                500,
        },
    );

    const matchingCsvHref =
        await page.evaluate(
            ({
                expectedRangeFrom,
                expectedRangeTo,
            }) => {
                const links = [
                    ...document.querySelectorAll(
                        'a[href^="csv/"]',
                    ),
                ];

                for (const link of links) {
                    const text =
                        (
                            link.textContent
                            ?? ''
                        ).trim();

                    if (
                        text.toUpperCase()
                        !== 'CSV'
                    ) {
                        continue;
                    }

                    const href =
                        link.getAttribute(
                            'href',
                        );

                    if (!href) {
                        continue;
                    }

                    try {
                        const csvUrl =
                            new URL(
                                href,
                                window.location.href,
                            );

                        if (
                            csvUrl.searchParams
                                .get('range_from')
                                === expectedRangeFrom
                            &&
                            csvUrl.searchParams
                                .get('range_to')
                                === expectedRangeTo
                            &&
                            csvUrl.searchParams
                                .get('expand_all')
                                === '1'
                        ) {
                            return href;
                        }
                    } catch {
                        // Ignore malformed href.
                    }
                }

                return null;
            },
            {
                expectedRangeFrom:
                    rangeFrom,

                expectedRangeTo:
                    rangeTo,
            },
        );

    if (!matchingCsvHref) {
        throw new Error(
            'Could not find Payroll CSV '
            + `matching ${rangeFrom} through `
            + `${rangeTo}.`,
        );
    }

    return new URL(
        matchingCsvHref,
        page.url(),
    );
}


/*
 * ---------------------------------------------------------
 * DOWNLOAD CSV
 * ---------------------------------------------------------
 */

async function downloadCsv(
    page,
    csvUrl,
    reportDescription,
) {
    log.info(
        `Downloading ${reportDescription} CSV.`,
    );

    const response =
        await page.request.get(
            csvUrl.href,
        );

    if (!response.ok()) {
        throw new Error(
            `${reportDescription} CSV request `
            + 'returned HTTP '
            + `${response.status()} `
            + `${response.statusText()}.`,
        );
    }

    const csvBuffer =
        await response.body();

    if (
        !csvBuffer
        || csvBuffer.length === 0
    ) {
        throw new Error(
            `${reportDescription} CSV is empty.`,
        );
    }

    log.info(
        `${reportDescription} CSV downloaded: `
        + `${csvBuffer.length} bytes`,
    );

    return csvBuffer;
}


/*
 * ---------------------------------------------------------
 * PYTHON PROCESSING
 * ---------------------------------------------------------
 */

async function processCsvsWithPython(
    shiftsCsvBuffer,
    wagesCsvBuffer,
    payrollCsvBuffer,
    location,
) {
    const shiftsTempPath =
        '/tmp/shifts.csv';

    const wagesTempPath =
        '/tmp/wages.csv';

    const payrollTempPath =
        '/tmp/payroll.csv';

    const processedTempPath =
        '/tmp/processed_shift_wages.csv';

    log.info(
        'Writing raw CSV buffers to temporary '
        + 'container storage.',
    );

    await Promise.all([
        writeFile(
            shiftsTempPath,
            shiftsCsvBuffer,
        ),

        writeFile(
            wagesTempPath,
            wagesCsvBuffer,
        ),

        writeFile(
            payrollTempPath,
            payrollCsvBuffer,
        ),
    ]);

    log.info(
        'Temporary raw CSV files created.',
    );

    /*
     * Verify process_reports.py exists before
     * attempting to execute it.
     */
    try {
        await access(
            PROCESS_REPORTS_PATH,
        );

        log.info(
            'Python processing script found: '
            + `${PROCESS_REPORTS_PATH}`,
        );
    } catch {
        throw new Error(
            'process_reports.py was not found at '
            + `${PROCESS_REPORTS_PATH}. `
            + 'Make sure process_reports.py exists '
            + 'in the same src directory as main.js '
            + 'and is copied into the Docker image.',
        );
    }

    log.info(
        'Running Python processing pipeline.',
    );

    let pythonResult;

    try {
        pythonResult =
            await execFileAsync(
                'python3',
                [
                    PROCESS_REPORTS_PATH,
                    shiftsTempPath,
                    wagesTempPath,
                    payrollTempPath,
                    processedTempPath,
                    location,
                ],
                {
                    timeout:
                        120_000,

                    maxBuffer:
                        10 * 1024 * 1024,
                },
            );
    } catch (error) {
        const stdout =
            error?.stdout
                ? String(error.stdout).trim()
                : '';

        const stderr =
            error?.stderr
                ? String(error.stderr).trim()
                : '';

        if (stdout) {
            log.error(
                `Python stdout before failure: ${stdout}`,
            );
        }

        if (stderr) {
            log.error(
                `Python stderr: ${stderr}`,
            );
        }

        throw new Error(
            'Python processing failed. '
            + `${error.message}`,
        );
    }

    const {
        stdout,
        stderr,
    } = pythonResult;

    if (stdout?.trim()) {
        log.info(
            `Python stdout: ${stdout.trim()}`,
        );
    }

    if (stderr?.trim()) {
        log.warning(
            `Python stderr: ${stderr.trim()}`,
        );
    }

    /*
     * Make sure Python actually created
     * the expected output file.
     */
    try {
        await access(
            processedTempPath,
        );
    } catch {
        throw new Error(
            'Python completed without creating '
            + `${processedTempPath}.`,
        );
    }

    const processedCsvBuffer =
        await readFile(
            processedTempPath,
        );

    if (
        !processedCsvBuffer
        || processedCsvBuffer.length === 0
    ) {
        throw new Error(
            'Python processing completed '
            + 'but the processed CSV is empty.',
        );
    }

    log.info(
        'Python processing completed successfully. '
        + `Processed CSV size: `
        + `${processedCsvBuffer.length} bytes`,
    );

    return processedCsvBuffer;
}


/*
 * ---------------------------------------------------------
 * MAIN
 * ---------------------------------------------------------
 */

try {
    log.info(
        'Reading Actor input...',
    );

    const input =
        await Actor.getInput();

    const {
        username,
        password,
        establishment = 'Leander'
    } = input ?? {};

    log.info(
        'Actor input received.',
        {
            hasUsername:
                Boolean(username),

            hasPassword:
                Boolean(password),

            establishment
        },
    );

    /*
     * -----------------------------------------------------
     * VALIDATE INPUT
     * -----------------------------------------------------
     */

    if (
        !username
        || !password
    ) {
        throw new Error(
            'Both username and password '
            + 'are required.',
        );
    }

    if (!establishment) {
        throw new Error(
            'An establishment/location '
            + 'is required.',
        );
    }


    const targetEstablishment =
        String(
            establishment,
        ).trim();

    /*
    * -----------------------------------------------------
    * GET REPORT DATE FROM PYTHON
    * -----------------------------------------------------
    */

    const revelDateFieldShift =
        await getSummaryShiftDateFromPython();

    log.info(
        'Using Python revel_date_field_shift: '
        + `${revelDateFieldShift}`,
    );

    /*
    * -----------------------------------------------------
    * CALCULATE MONDAY-SUNDAY WEEK
    * -----------------------------------------------------
    */

    const {
        rangeFrom,
        rangeTo,
    } = getWeekRange(
        revelDateFieldShift,
    );

    log.info(
        'Calculated Revel work week: '
        + `${rangeFrom} through `
        + `${rangeTo}`,
    );
    

    log.info(
        'Requested establishment: '
        + `${targetEstablishment}`,
    );

    /*
     * -----------------------------------------------------
     * BUILD REPORT URLS
     * -----------------------------------------------------
     */

    const timeWorkedReportUrl =
        buildTimeWorkedReportUrl(
            rangeFrom,
            rangeTo,
        );

    const payrollReportUrl =
        buildPayrollReportUrl(
            rangeFrom,
            rangeTo,
        );

    log.info(
        'Generated Time Worked report URL: '
        + `${timeWorkedReportUrl}`,
    );

    log.info(
        'Generated Payroll report URL: '
        + `${payrollReportUrl}`,
    );

    /*
     * -----------------------------------------------------
     * OUTPUT STORE
     * -----------------------------------------------------
     */

    const csvStore =
        await Actor.openKeyValueStore(
            CSV_STORE_ID,
        );

    const filePrefix =
        `${
            formatEstablishmentForKey(
                targetEstablishment,
            )
        }`
        + '_'
        + `${
            formatDateForFilename(
                rangeFrom,
            )
        }`
        + '_THRU_'
        + `${
            formatDateForFilename(
                rangeTo,
            )
        }`;

    const processedCsvStorageKey =
        `${filePrefix}`
        + '_PROCESSED_SHIFT_WAGES.csv';

    log.info(
        'Final processed CSV storage key: '
        + `${processedCsvStorageKey}`,
    );

    /*
     * Tracks whether the request handler actually
     * completed successfully.
     *
     * This prevents a failedRequestHandler from
     * resulting in a misleading successful Actor run.
     */
    let processingSucceeded =
        false;

    /*
     * -----------------------------------------------------
     * CRAWLER
     * -----------------------------------------------------
     */

    const crawler =
        new PlaywrightCrawler({
            maxRequestsPerCrawl:
                1,

            maxRequestRetries:
                0,

            requestHandlerTimeoutSecs:
                420,

            async requestHandler({
                page,
                request,
            }) {
                /*
                 * =========================================
                 * LOGIN
                 * =========================================
                 */

                log.info(
                    'Opening Revel portal: '
                    + `${request.url}`,
                );

                await page.goto(
                    request.url,
                    {
                        waitUntil:
                            'domcontentloaded',

                        timeout:
                            30_000,
                    },
                );

                await saveScreenshot(
                    page,
                    'REVEL_LOGIN_START',
                );

                const usernameField =
                    page.locator(
                        '#username',
                    );

                await usernameField.waitFor({
                    state:
                        'visible',

                    timeout:
                        15_000,
                });

                await usernameField.fill(
                    username,
                );

                log.info(
                    'Username entered. '
                    + 'Clicking Continue.',
                );

                await page
                    .getByRole(
                        'button',
                        {
                            name:
                                'Continue',

                            exact:
                                true,
                        },
                    )
                    .click();

                const passwordField =
                    page.locator(
                        'input[type="password"]',
                    );

                await passwordField.waitFor({
                    state:
                        'visible',

                    timeout:
                        20_000,
                });

                await saveScreenshot(
                    page,
                    'REVEL_PASSWORD_STEP',
                );

                await passwordField.fill(
                    password,
                );

                const loginButton =
                    page
                        .locator(
                            'button[type="submit"]:visible, '
                            + 'input[type="submit"]:visible',
                        )
                        .last();

                await loginButton.waitFor({
                    state:
                        'visible',

                    timeout:
                        15_000,
                });

                const buttonText =
                    (
                        await loginButton
                            .textContent()
                    )?.trim()
                    || (
                        await loginButton
                            .getAttribute(
                                'value',
                            )
                    )
                    || 'Submit';

                log.info(
                    'Clicking final login button: '
                    + `${buttonText}`,
                );

                await loginButton.click();

                await passwordField.waitFor({
                    state:
                        'hidden',

                    timeout:
                        30_000,
                });

                await page.waitForLoadState(
                    'domcontentloaded',
                );

                log.info(
                    'Login completed. '
                    + `Current URL: ${page.url()}`,
                );

                /*
                 * =========================================
                 * NAVIGATE TO TIME WORKED
                 * =========================================
                 */

                if (
                    !page.url().includes(
                        '/schedules/timeworked',
                    )
                ) {
                    log.info(
                        'Navigating to Time Worked report.',
                    );

                    await page.goto(
                        REVEL_TIME_WORKED_URL,
                        {
                            waitUntil:
                                'domcontentloaded',

                            timeout:
                                30_000,
                        },
                    );
                }

                /*
                * =========================================
                * SELECT ESTABLISHMENT
                * =========================================
                */

                const establishmentText =
                    page.locator(
                        '[data-cy="header-establishment-text"]',
                    );

                await establishmentText.waitFor({
                    state:
                        'visible',

                    timeout:
                        20_000,
                });

                const currentEstablishment =
                    (
                        await establishmentText
                            .textContent()
                    )?.trim()
                    || 'Unknown';

                log.info(
                    'Current establishment '
                    + 'before selection: '
                    + `${currentEstablishment}`,
                );

                /*
                * Only open the establishment selector if the
                * currently selected store does not match the
                * requested store.
                */
                if (
                    !currentEstablishment
                        .toLowerCase()
                        .includes(
                            targetEstablishment
                                .toLowerCase(),
                        )
                ) {
                    log.info(
                        'Current establishment does not match '
                        + `requested establishment "${targetEstablishment}". `
                        + 'Opening establishment selector.',
                    );

                    await establishmentText.click();

                    /*
                    * =========================================
                    * FORCE "ESTAB. NO." SORTING
                    * =========================================
                    */

                    const establishmentNumberButton =
                        page.locator(
                            'div.btn.by-id[data-sorting="id"]',
                        );

                    await establishmentNumberButton.waitFor({
                        state:
                            'visible',

                        timeout:
                            20_000,
                    });

                    log.info(
                        'Clicking "Estab. No." sorting option.',
                    );

                    await establishmentNumberButton.click();

                    await page.waitForTimeout(
                        500,
                    );

                    /*
                    * =========================================
                    * EXPAND ALL ESTABLISHMENT FOLDERS
                    * =========================================
                    */

                    const expandAllButton =
                        page.locator(
                            'span.expand-all',
                        );

                    await expandAllButton.waitFor({
                        state:
                            'visible',

                        timeout:
                            20_000,
                    });

                    log.info(
                        'Clicking "expand all" '
                        + 'in establishment selector.',
                    );

                    await expandAllButton.click();

                    /*
                    * Give FancyTree a moment to finish
                    * rendering all expanded children.
                    */
                    await page.waitForTimeout(
                        1_500,
                    );

                    await saveScreenshot(
                        page,
                        'REVEL_ESTABLISHMENT_EXPANDED',
                    );

                    /*
                    * =========================================
                    * FIND REQUESTED STORE
                    * =========================================
                    */

                    const escapedEstablishment =
                        escapeRegExp(
                            targetEstablishment,
                        );

                    /*
                    * With "Estab. No." sorting enabled the
                    * tree label may look like:
                    *
                    *     42 | Leander
                    *
                    * This regex accepts either:
                    *
                    *     Leander
                    *
                    * or:
                    *
                    *     42 | Leander
                    */
                    const establishmentPattern =
                        new RegExp(
                            '(?:^|\\|\\s*)'
                            + escapedEstablishment
                            + '\\s*$',
                            'i',
                        );

                    const establishmentOptions =
                        page
                            .locator(
                                'span.fancytree-title',
                            )
                            .filter({
                                hasText:
                                    establishmentPattern,
                            });

                    const matchingCount =
                        await establishmentOptions.count();

                    log.info(
                        'Matching establishment options '
                        + `for "${targetEstablishment}": `
                        + `${matchingCount}`,
                    );

                    if (matchingCount === 0) {
                        await saveScreenshot(
                            page,
                            'REVEL_ESTABLISHMENT_NOT_FOUND',
                        );

                        throw new Error(
                            'Could not find requested Revel '
                            + `establishment "${targetEstablishment}" `
                            + 'after expanding all establishment folders.',
                        );
                    }

                    const establishmentOption =
                        establishmentOptions.first();

                    await establishmentOption.waitFor({
                        state:
                            'visible',

                        timeout:
                            30_000,
                    });

                    const optionText =
                        (
                            await establishmentOption
                                .textContent()
                        )?.trim()
                        || targetEstablishment;

                    log.info(
                        'Selecting Revel establishment: '
                        + `${optionText}`,
                    );

                    await establishmentOption.click();

                    /*
                    * Wait until the header locator shows the
                    * requested store. This is the same locator
                    * that read the pre-click header. An in-page
                    * document.querySelector cannot see that node,
                    * so it kept timing out after the switch had
                    * already completed.
                    */
                    await establishmentText
                        .filter({
                            hasText:
                                targetEstablishment,
                        })
                        .waitFor({
                            state:
                                'visible',

                            timeout:
                                30_000,
                        });

                    await page.waitForTimeout(
                        1_000,
                    );
                }

                /*
                * =========================================
                * FINAL ESTABLISHMENT VALIDATION
                * =========================================
                */

                const establishmentAfterSelection =
                    (
                        await establishmentText
                            .textContent()
                    )?.trim()
                    || '';

                log.info(
                    'Establishment header '
                    + 'after selection process: '
                    + `${establishmentAfterSelection}`,
                );

                /*
                * This is the safety check that prevents
                * data for one store from being written
                * under another store's name.
                */
                if (
                    !establishmentAfterSelection
                        .toLowerCase()
                        .includes(
                            targetEstablishment
                                .toLowerCase(),
                        )
                ) {
                    await saveScreenshot(
                        page,
                        'REVEL_ESTABLISHMENT_VALIDATION_FAILURE',
                    );

                    throw new Error(
                        'Revel establishment validation failed. '
                        + `Requested "${targetEstablishment}", `
                        + 'but the establishment header shows '
                        + `"${establishmentAfterSelection}". `
                        + 'Aborting before any reports are downloaded '
                        + 'or written to Supabase.',
                    );
                }

                log.info(
                    'Establishment successfully validated: '
                    + `${establishmentAfterSelection}`,
                );

                await saveScreenshot(
                    page,
                    'REVEL_ESTABLISHMENT_SELECTED',
                );

                /*
                 * =========================================
                 * LOAD TIME WORKED SHIFT REPORT
                 * =========================================
                 */

                log.info(
                    'Loading Time Worked report '
                    + `for ${rangeFrom} through `
                    + `${rangeTo}.`,
                );

                await page.goto(
                    timeWorkedReportUrl,
                    {
                        waitUntil:
                            'domcontentloaded',

                        timeout:
                            30_000,
                    },
                );

                await page
                    .waitForLoadState(
                        'networkidle',
                        {
                            timeout:
                                30_000,
                        },
                    )
                    .catch(() => {
                        log.info(
                            'Time Worked page '
                            + 'did not become fully '
                            + 'network idle. Continuing.',
                        );
                    });

                /*
                 * =========================================
                 * EXPORT #1
                 * SHIFTS
                 * =========================================
                 */

                const shiftsCsvUrl =
                    await waitForTimeWorkedCsvHref(
                        page,
                        'shift',
                        rangeFrom,
                        rangeTo,
                    );

                log.info(
                    'Shifts CSV URL: '
                    + `${shiftsCsvUrl.href}`,
                );

                const shiftsCsvBuffer =
                    await downloadCsv(
                        page,
                        shiftsCsvUrl,
                        'Employee Time Worked Shifts',
                    );

                log.info(
                    'Shifts CSV retained in memory.',
                );

                /*
                 * =========================================
                 * EXPORT #2
                 * WAGES
                 * =========================================
                 */

                log.info(
                    'Switching Time Worked report '
                    + 'to Wage view.',
                );

                const wageRadio =
                    page.locator(
                        'input[type="radio"]'
                        + '[name="rows_types"]'
                        + '[value="wage"]',
                    );

                await wageRadio.waitFor({
                    state:
                        'attached',

                    timeout:
                        20_000,
                });

                if (
                    !(await wageRadio.isChecked())
                ) {
                    await wageRadio.check({
                        force:
                            true,
                    });
                }

                await page.waitForFunction(
                    () => {
                        const radio =
                            document.querySelector(
                                'input[type="radio"]'
                                + '[name="rows_types"]'
                                + '[value="wage"]',
                            );

                        return Boolean(
                            radio
                            && radio.checked,
                        );
                    },
                    {
                        timeout:
                            20_000,

                        polling:
                            250,
                    },
                );

                const wagesCsvUrl =
                    await waitForTimeWorkedCsvHref(
                        page,
                        'wage',
                        rangeFrom,
                        rangeTo,
                    );

                log.info(
                    'Wages CSV URL: '
                    + `${wagesCsvUrl.href}`,
                );

                const wagesCsvBuffer =
                    await downloadCsv(
                        page,
                        wagesCsvUrl,
                        'Employee Time Worked Wages',
                    );

                log.info(
                    'Wages CSV retained in memory.',
                );

                /*
                 * =========================================
                 * EXPORT #3
                 * PAYROLL
                 * =========================================
                 */

                log.info(
                    'Navigating to Payroll report.',
                );

                await page.goto(
                    payrollReportUrl,
                    {
                        waitUntil:
                            'domcontentloaded',

                        timeout:
                            30_000,
                    },
                );

                await page
                    .waitForLoadState(
                        'networkidle',
                        {
                            timeout:
                                30_000,
                        },
                    )
                    .catch(() => {
                        log.info(
                            'Payroll page did not '
                            + 'become fully network '
                            + 'idle. Continuing.',
                        );
                    });

                const payrollCsvUrl =
                    await waitForPayrollCsvHref(
                        page,
                        rangeFrom,
                        rangeTo,
                    );

                log.info(
                    'Payroll CSV URL: '
                    + `${payrollCsvUrl.href}`,
                );

                const payrollCsvBuffer =
                    await downloadCsv(
                        page,
                        payrollCsvUrl,
                        'Payroll',
                    );

                log.info(
                    'Payroll CSV retained in memory.',
                );

                /*
                 * =========================================
                 * PROCESS ALL 3 RAW REPORTS
                 * =========================================
                 */

                log.info(
                    'All three raw reports downloaded. '
                    + 'Starting Python processing.',
                );

                const processedCsvBuffer =
                    await processCsvsWithPython(
                        shiftsCsvBuffer,
                        wagesCsvBuffer,
                        payrollCsvBuffer,
                        targetEstablishment,
                    );

                /*
                 * =========================================
                 * SAVE ONLY FINAL PROCESSED OUTPUT
                 * =========================================
                 */

                await csvStore.setValue(
                    processedCsvStorageKey,
                    processedCsvBuffer,
                    {
                        contentType:
                            'text/csv; charset=utf-8',
                    },
                );

                log.info(
                    'Saved final processed CSV '
                    + `to KV store ${CSV_STORE_ID}: `
                    + `${processedCsvStorageKey}`,
                );

                /*
                 * =========================================
                 * SUCCESS METADATA
                 * =========================================
                 */

                await Actor.pushData({
                    status:
                        'success',

                    establishment:
                        targetEstablishment,

                    report:
                        'Processed Employee Shift Wages',

                    inputDate:
                        revelDateFieldShift,

                    dateSource:
                        'get_shift_summary_date.py',

                    rangeFrom,

                    rangeTo,

                    rawReports: {
                        shiftsBytes:
                            shiftsCsvBuffer.length,

                        wagesBytes:
                            wagesCsvBuffer.length,

                        payrollBytes:
                            payrollCsvBuffer.length,
                    },

                    processedCsv: {
                        storageKey:
                            processedCsvStorageKey,

                        sizeBytes:
                            processedCsvBuffer.length,
                    },

                    keyValueStoreId:
                        CSV_STORE_ID,

                    timestamp:
                        new Date()
                            .toISOString(),

                    message:
                        'Downloaded Time Worked Shifts, '
                        + 'Time Worked Wages, and Payroll '
                        + 'reports, processed them with '
                        + 'Python, and saved only the '
                        + 'final processed CSV.',
                });

                /*
                 * IMPORTANT:
                 * Only set this after EVERYTHING
                 * completes successfully.
                 */
                processingSucceeded =
                    true;

                log.info(
                    'Revel employee processing '
                    + 'completed successfully.',
                );
            },

            /*
             * =============================================
             * FAILURE HANDLER
             * =============================================
             */

            async failedRequestHandler(
                {
                    page,
                    request,
                },
                error,
            ) {
                log.error(
                    'Revel employee processing failed: '
                    + `${error.message}`,
                );

                if (page) {
                    try {
                        await saveScreenshot(
                            page,
                            'REVEL_EMPLOYEE_PROCESSING_FAILURE',
                        );
                    } catch (
                        screenshotError
                    ) {
                        log.warning(
                            'Unable to save failure screenshot: '
                            + screenshotError.message,
                        );
                    }
                }

                await Actor.pushData({
                    status:
                        'failed',

                    portalUrl:
                        page
                            ? page.url()
                            : request.url,

                    establishment:
                        targetEstablishment,

                    inputDate:
                        revelDateFieldShift,

                    dateSource:
                        'get_shift_summary_date.py',

                    rangeFrom,

                    rangeTo,

                    processedCsvStorageKey,

                    keyValueStoreId:
                        CSV_STORE_ID,

                    timestamp:
                        new Date()
                            .toISOString(),

                    message:
                        error.message,
                });
            },
        });

    /*
     * -----------------------------------------------------
     * RUN
     * -----------------------------------------------------
     */

    log.info(
        'Starting Revel employee '
        + 'processing crawler...',
    );

    await crawler.run([
        REVEL_TIME_WORKED_URL,
    ]);

    /*
     * failedRequestHandler does not necessarily cause
     * crawler.run() to throw.
     *
     * Therefore, explicitly verify the handler completed.
     */
    if (!processingSucceeded) {
        throw new Error(
            'Revel extraction or processing failed. '
            + 'See the Actor logs for the original error.',
        );
    }

    log.info(
        'Crawler and processing '
        + 'finished successfully.',
    );

    await Actor.exit();

} catch (error) {
    log.error(
        'Actor failed before completion.',
        {
            message:
                error?.message,

            stack:
                error?.stack,
        },
    );

    throw error;
}