import requests
import numpy as np
import pandas as pd
import time
from warnings import warn
from bs4 import BeautifulSoup
from datetime import datetime

class ScraperAthensClarke(object):
    """Webscraper for Athens-Clarke county jail, GA"""
       
    def __init__(self, url, timeout=10, retries=0, sleep_sec=5):
        self.url = url # entry URL from which to start scraping
        self.timeout = timeout # seconds timeout to avoid infinite waiting
        self.retries = retries # how many times to retry loading if there's http errors.
        self.sleep_sec = sleep_sec # seconds to wait between subpage requests to avoid overloading server
        response = requests.head(url=self.url) # set user-agent so they can contact us if they don't like how we're scraping
        self.headers = {'User-Agent' : response.request.headers['User-Agent'] + ' (Contact lahoffm@gmail.com, https://github.com/lahoffm/aclu-bail-reform)'}
        
    def scrape_all(self):
        """ Scrape main page then each inmate's subpage with more details.
            Assemble results into pandas dataframe in standard format and dump to CSV file."""
        self.scrape_main()
        self.scrape_sub()
        self.dump()

    def scrape_main(self):
        """Get main page table into data frame formatted for the output CSV"""
        
        # Get main page table into data frame
        self.html_main, errormsg = self.get_page(self.url) # will be used to get subpage links
        if errormsg is not None:
            raise requests.RequestException('Could not get main jail page. Error message: ' + errormsg)
        df_list = pd.read_html(self.html_main, header=0, converters={'MID#': str,
                                                           'NAME': str,
                                                           'SEX': str,
                                                           'RACE': str,
                                                           'BOOKING DATE': str,
                                                           'CHARGE': str,
                                                           'BOND AMOUNT': str,
                                                           'CASE NUMBER': str,
                                                           'POLICE CASE#': str,
                                                           'YEAR OF BIRTH': str,
                                                           'VISITATION': str})
        assert len(df_list) == 1, 'Should be only 1 table on page'
        df_main = df_list[0] 
        df_main = df_main.iloc[1:,:] # drop 1st row, it's all NaN because site had an extra TR tag.
        df_main.reset_index(drop=True, inplace=True) # so indexes equal to self.df indexes
        assert all(df_main.columns==['MID#', 'NAME', 'SEX', 'RACE', 'BOOKING DATE', 'CHARGE', 'BOND AMOUNT', 'CASE NUMBER', 'POLICE CASE#', 'YEAR OF BIRTH', 'VISITATION']), 'Column names have changed'

        # This will go in CSV file.
        self.df = pd.DataFrame(np.zeros((df_main.shape[0], 25)), columns=[
                  'county_name', # yes
                  'timestamp', # yes - time main page was scraped
                  'url', # yes - subpage urls (if couldn't connect, it's the main page url)
                  'inmate_id', # yes
                  'inmate_lastname', # yes
                  'inmate_firstname', # yes
                  'inmate_middlename', # yes
                  'inmate_sex', # yes
                  'inmate_race', # yes
                  'inmate_age', # yes - the age they turn on their birthdays in current year
                  'inmate_dob', # yes - only the year
                  'inmate_address', # yes - in subpages
                  'booking_timestamp', # yes
                  'release_timestamp',
                  'processing_numbers', # yes
                  'agency', # yes - in subpages
                  'facility', # yes
                  'charges', # yes - in subpages
                  'severity', # yes - in subpages
                  'bond_amount', # yes - in subpages
                  'current_status', # yes - in subpages
                  'court_dates',
                  'days_jailed',
                  'other',
                  'notes']) # yes
        self.df[:] = '' # unfilled columns will be written to CSV as empty strings

        # Set county name, facility, url, timestamp
        # url will be overwritten with subpage's url unless we get HTTP errors
        # timestamp will be overwritten with subpage's timestamp unless we get HTTP errors
        self.df['county_name'] = 'athens-clarke'
        self.df['facility'] = 'Clarke County Jail' # only one jail in this county, apparently
        self.df['url'] = self.url
        self.df['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S EST')  # hardcoded EST because it's not critical to get the hour correct,
                                                                              # timestamps are just for knowing roughly when we scraped.

        # Set inmate ID
        assert all(pd.notnull(df_main['MID#']))
        assert df_main['MID#'].unique().size == df_main.shape[0], 'Non-unique inmate IDs' # check inmate IDs are unique so matching works in scrape_sub
        self.df['inmate_id'] = df_main['MID#']

        # Set inmate last/first/middle name. Assumes format is "lastname, firstname zero or more middle names"
        assert all(pd.notnull(df_main['NAME']))
        inmate_name = df_main['NAME'].str.split(', ', n=1, expand=True)
        assert inmate_name.shape[1] == 2, 'Invalid name format'
        self.df['inmate_lastname'] = inmate_name.iloc[:,0]
        inmate_name = inmate_name.iloc[:,1]
        inmate_name = inmate_name.str.split(' ', n=1, expand=True)
        assert inmate_name.shape[1] == 2, 'Invalid name format'
        inmate_name.fillna('', inplace=True)
        self.df['inmate_firstname'] = inmate_name.iloc[:,0]
        self.df['inmate_middlename'] = inmate_name.iloc[:,1]
        
        # Set inmate sex
        assert all(pd.notnull(df_main['SEX']))
        assert np.isin(df_main['SEX'].unique(), np.array(['MALE','FEMALE'])).all(), "Invalid sex format"
        self.df['inmate_sex'] = df_main['SEX'].str.lower().str[0]
        
        # Set inmate race
        assert all(pd.notnull(df_main['RACE']))
        inmate_race = df_main['RACE'].str.lower() # don't have to convert 'asian' or 'white' because already listed that way
        inmate_race = inmate_race.str.replace('black/african american', 'black')
        inmate_race = inmate_race.str.replace('hispanic or latino', 'hispanic')
        inmate_race = inmate_race.str.replace('middle eastern decent', 'middle-eastern') # they had a typo
        assert np.isin(inmate_race.unique(), np.array(['asian','white','black','hispanic','middle-eastern'])).all(),\
                     "One or more of these races not converted to standard format: " + str(inmate_race.unique())
        self.df['inmate_race'] = inmate_race
        
        # Set booking timestamp
        # Hardcoding 'EST' because Athens-Clarke county is in Georgia USA
        # See https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior
        # Outputs 'YYYY-MM-DD HH:MM:SS EST', see https://docs.python.org/3/library/datetime.html#datetime.datetime.__str__             
        assert all(pd.notnull(df_main['BOOKING DATE']))
        convert_dt = lambda dt : str(datetime.strptime(dt, "%m/%d/%Y %I:%M:%S %p")) + ' EST' # Format changes will likely be caught here
        self.df['booking_timestamp'] = df_main['BOOKING DATE'].apply(convert_dt)
        
        # Not setting charges or bond_amount from main page, because more detailed info found in subpages

        # Set case number - many are blank for Athens-Clarke county
        # Replace semicolons so ETL code doesn't think they are multiple processing numbers
        # Format is 'Case # XXX;Police case # XXX'. If one or both are missing there is still a ';'
        #   so ETL parser knows what's missing.
        self.df['processing_numbers'] = 'Case # ' + df_main['CASE NUMBER'].str.replace(';',':')
        self.df['processing_numbers'].fillna('', inplace=True)
        tmp = ';Police case # ' + df_main['POLICE CASE#'].str.replace(';',':')
        tmp.fillna(';', inplace=True)
        self.df['processing_numbers'] = self.df['processing_numbers'].str.cat(tmp)
        
        # Set inmate date of birth - only the year is available for Athens-Clarke county
        assert all(pd.notnull(df_main['YEAR OF BIRTH']))
        assert all(df_main['YEAR OF BIRTH'].str.len()==4), 'Invalid year of birth format'
        self.df['inmate_dob'] = df_main['YEAR OF BIRTH']
        
        # Set inmate age when we scraped the site
        # Because we only have birth year, age is whatever age they turn on their birthdays in the current year.
        # That means it's impossible for 18 year olds (minimum booking age) to be incorrectly assigned age 17.
        calc_age = lambda yyyy : str(datetime.now().year - int(yyyy))
        self.df['inmate_age'] = df_main['YEAR OF BIRTH'].apply(calc_age)
        
    def scrape_sub(self):
        """ Scrape each inmate's details page - links from main page.
            Add data to the inmate's row in self.df.
            Retry self.retries times if there's any errors, then skip to next
            subpage. If subpage couldn't be loaded, log it in the "notes" field.
        """
        soup = BeautifulSoup(self.html_main, 'lxml') # self.scrape_main() should be called before this
        assert len(list(soup.find_all('a', href=True))) == self.df.shape[0], "Number of hrefs != number of table entries"
        self.df['notes'] = 'Failed to load inmate detail page. Leaving some fields blank' # will be erased ONLY when page successfully loads
        
        i = 0
        for a in soup.find_all('a', href=True): # scrape each subpage (which have additional details for a single inmate)
            # Get subpage
            # The tag's format should be
            #      <a href="##" onclick="window.open('detailsNEW.asp?id=-59091&amp;bid=2017-00004550&amp;pg=1&amp;curr=yes',
            #      'search', 'width=730,height=800,status=yes,resizable=yes,scrollbars=yes')">ABOYADE, OLUFEMI BABALOLA</a>
            i = i + 1
            print('Downloading subpage {0} of {1}...'.format(i, self.df.shape[0]))
            subpage_url = self.url[0:self.url.rfind('/')+1] + a["onclick"].split(',')[0].split("'")[1]
            html_sub, errormsg = self.get_page(subpage_url)
            if errormsg is not None:
                warn('Could not get URL "' + subpage_url + '" for subpage ' + str(i) + '. Error message: ' + errormsg + '. Continuing to next page')
                continue
            
            # Get subpage's 2 tables in dataframes
            try:
                df_list = pd.read_html(html_sub, match="Name:", converters={0: str, 1: str, 2: str, 3: str})
            except ValueError as e:
                warn(str(e) +' for subpage ' + str(i) + ', continuing to next page.')
                continue
            assert len(df_list) == 1, "Should be only 1 table on page with matching text."
            df_sub1 = df_list[0]
            assert df_sub1.shape==(5,4), 'Wrong table dimensions'
            if pd.isnull(df_sub1.loc[1,1]): df_sub1.loc[1,1]='' # for blank addresses
            assert  df_sub1.iloc[0,0]=='Name:' and df_sub1.iloc[1,0]=='Address:' and df_sub1.iloc[2,0]=='Sex:'\
                and df_sub1.iloc[3,0]=='Year of Birth:' and df_sub1.iloc[4,0]=='Booking Date/Time:' and pd.notnull(df_sub1.iloc[:,1]).all()\
                and df_sub1.iloc[0,2].startswith('MID#: ') and pd.isnull(df_sub1.iloc[1,2]) and df_sub1.iloc[2,2]=='Race:'\
                and df_sub1.iloc[3,2]=='Height/Weight:' and df_sub1.iloc[4,2]=='Released Date/Time:' and pd.isnull(df_sub1.iloc[0,3])\
                and pd.isnull(df_sub1.iloc[1,3]) and pd.notnull(df_sub1.iloc[2:5,3]).all(), 'Table format has changed'
            df_list = pd.read_html(html_sub, header=0, match="ARRESTING AGENCY", converters={0: str, 1: str, 2: str, 3: str, 4: str, 5: str, 6: str})
            assert len(df_list) == 1, "Should be only 1 table on page with matching text."
            df_sub2 = df_list[0]
            assert all(df_sub2.columns==['ARRESTING AGENCY', 'GRADE OF CHARGE', 'CHARGE DESCRIPTION', 'BOND AMOUNT', 'BOND REMARKS', 'BOND LAST UPDATED', 'DISPOSITION']), 'Column names have changed'
            assert not df_sub2.empty, 'Table has zero rows'
            
            # Use inmate id to find matching self.df row where we will insert data
            inmate_id = df_sub1.iloc[0,2][6:] # checked above that it starts with 'MID#: ' 
            ix = self.df.index[self.df['inmate_id']==inmate_id] # checked in scrape_main that inmate IDs are all unique, so this will match 1 row
            assert not self.df.loc[ix].empty, 'Inmate id "' + inmate_id + '" not found in main page'
            
            # Set URL
            self.df.loc[ix,'url'] = subpage_url
            
            # Set inmate address
            self.df.loc[ix, 'inmate_address'] = df_sub1.iloc[1,1]
            
            # Set agency
            assert all(pd.notnull(df_sub2['ARRESTING AGENCY'])), 'Invalid arresting agency format'
            try:
                assert len(df_sub2['ARRESTING AGENCY'].unique())==1, 'Invalid arresting agency format'
            except AssertionError as e:
                 warn(str(e) + ", multiple arresting agencies for subpage " + str(i) + ". Inserting the agency that made the arrest for each charge")
                 df_sub2.loc[0, 'ARRESTING AGENCY'] = df_sub2['ARRESTING AGENCY'].str.cat(sep=';')
            self.df.loc[ix, 'agency'] = df_sub2.loc[0, 'ARRESTING AGENCY']                                   
            
            # Set charge severity.
            # Sometimes one "grade of charge" is NaN, means they are holding the inmate for some other county.
            # We still put in the same fields, but the "grade of charge" will be an empty string.
            df_sub2.fillna('', inplace=True) # important for later fields so semicolons go in right places
            if any(df_sub2['GRADE OF CHARGE'] == 'L'): # don't know what 'L' means, it's not misdemeanor or felony
                df_sub2['GRADE OF CHARGE'] = df_sub2['GRADE OF CHARGE'].str.replace('L','')
                warn("Unknown grade of charge 'L' for subpage " + str(i) + ". Replacing with ''")

            assert np.isin(df_sub2['GRADE OF CHARGE'].unique(), np.array(['M','F',''])).all(), 'Invalid misdemeanor/felony format.'
            df_sub2['GRADE OF CHARGE'] = df_sub2['GRADE OF CHARGE'].str.replace('M','misdemeanor')
            df_sub2['GRADE OF CHARGE'] = df_sub2['GRADE OF CHARGE'].str.replace('F','felony')
            self.df.loc[ix, 'severity'] = df_sub2['GRADE OF CHARGE'].str.cat(sep=';')

            # Set charges, separated by ';' even if charge description is empty string
            # Have to replace ';' with ':' first to prevent bug
            self.df.loc[ix,'charges'] = df_sub2['CHARGE DESCRIPTION'].str.replace(';',':').str.cat(sep=';')

            # Set bond amount for each charge, separated by ';'.
            # If bond amount is '$0.00', '$' or '' there will be no dollar amount starting the bond_amount field,
            # just ' ' then the bond remarks / bond last updated.
            df_sub2.loc[df_sub2['BOND AMOUNT']=='$0.00','BOND AMOUNT'] = '' # Usually indicates no bond, not "released without bond"
            df_sub2.loc[df_sub2['BOND AMOUNT']=='$', 'BOND AMOUNT'] = '' # Sometimes this happens, not sure what it means
            df_sub2['BOND AMOUNT'] = df_sub2['BOND AMOUNT'].str.replace(',','') # Replace $1,000.00 with $1000.00
            df_sub2['BOND REMARKS'] = df_sub2['BOND REMARKS'].str.replace(';',':')
            df_sub2['BOND LAST UPDATED'] = df_sub2['BOND LAST UPDATED'].str.replace(';',':')
            self.df.loc[ix,'bond_amount'] = df_sub2['BOND AMOUNT'].str.cat([df_sub2['BOND REMARKS'],
                                                                      [' Bond last updated']*df_sub2.shape[0],
                                                                      df_sub2['BOND LAST UPDATED']],
                                                                      sep=' ').str.cat(sep=';')

            # Set status for each charge like 'SENTENCED'. For this site, most statuses are blank.
            self.df.loc[ix, 'current_status'] = df_sub2['DISPOSITION'].str.replace(';',':').str.cat(sep=';')
                        
            # Set notes
            self.df.loc[ix,'notes'] = ''

            time.sleep(self.sleep_sec)
    
    def dump(self):
        csv_fname = datetime.now().strftime('athens-clarke_current-inmate-roster_%Y_%m_%d_%H_%M_%S.csv')
        csv_dir = '../../../data'
        self.df.to_csv(csv_dir + '/' + csv_fname, index=False, line_terminator='\n') # matches default params for csv.writer
        print('Wrote ' + csv_fname + ' to ' + csv_dir)
    
    def get_page(self, url):
        """ Get html for a single page. If errors, retries loading page self.retries times.
            Returns html text (None if errors) 
            and error message (None if no errors)"""
        retries_left = self.retries
        while retries_left >= 0:
            try:
                response = requests.get(url, timeout=self.timeout, headers=self.headers)
                if response.status_code != requests.codes.ok:
                    raise requests.HTTPError('HTTP response code was {0}, should have been {1}'.format(response.status_code, requests.codes.ok))
                return response.text, None # no errors
            except Exception as e:
                errormsg = str(e)
                retries_left = retries_left - 1
        return None, errormsg # errors on the last attempt
    