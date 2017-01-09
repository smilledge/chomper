import six

from chomper.exceptions import NotConfigured, ItemNotImportable
from . import Exporter

try:
    import psycopg2
    from psycopg2 import ProgrammingError, DatabaseError, errorcodes
except ImportError:
    raise NotConfigured('Psycopg2 library not installed')


class BasePostgresExporter(Exporter):

    def __init__(self, table, database, user, password, host='localhost', port=5432):
        """
        Base class for all Postgres exporters

        :param table: Name of the database table this exporter with interact with
        :param database: Postgres database name
        :param user: Postgres user
        :param password: Postgres user password
        :param host: Postgres host
        :param port: Postgres post
        """
        self.table = table
        self.database = database
        self.connection_args = dict(database=database, user=user, password=password, host=host, port=port)
        self.connection = psycopg2.connect(**self.connection_args)
        self.connection.set_client_encoding('utf-8')
        self.check_postgres_version()

    def check_postgres_version(self):
        """
        Show a warning if we're using a version of postgres < 9.5
        """
        min_version = (9, 5)
        cursor = self.connection.cursor()
        try:
            cursor.execute("select current_setting('server_version')")
            result = cursor.fetchone()
        except DatabaseError:
            return None
        else:
            version_str = result[0]
            version = tuple([int(num) for num in version_str.split('.')])
            if version < min_version:
                self.logger.warn('Postgres version > 9.5 is recommended. You are currently using v%s' % version_str)
        finally:
            cursor.close()

    def table_columns(self):
        """
        Get a list of all columns in a table
        """
        cursor = self.connection.cursor()
        sql = 'SELECT column_name FROM information_schema.columns WHERE table_name = %s'
        try:
            cursor.execute(sql, [self.table])
            results = cursor.fetchall()
        except DatabaseError:
            return None
        else:
            return [result[0] for result in results]
        finally:
            cursor.close()

    def item_columns(self, item, columns=None, protected_columns=None):
        """
        Get a list of column names that can be updated for an item

        If column names are explicitly defined we will use them.
        Otherwise, we will use the tables column names defined in the database
        that are also defined on the item.
        Finally, fallback to all keys defined on the item.
        """
        if protected_columns is None:
            protected_columns = []

        item_keys = item.keys()

        if columns:
            return [col for col in columns if col in item_keys and col not in protected_columns]
        else:
            return item_keys

    def handle_error(self, e):
        """
        Handle a Postgres error

        Drops the item from the pipeline and attempts to return a more using friendly message.

        :param e: psycopg2 DatabaseError object
        """
        if e.pgcode == errorcodes.UNDEFINED_TABLE:
            raise ItemNotImportable('Unable to insert item into table "%s". Table does not exist in database "%s".'
                                    % (self.table, self.database))
        else:
            raise ItemNotImportable('Unable to insert item into Postgres database: \n%r.' % e.pgerror)

    def close(self):
        # TODO: importer needs to call close on all exporters when finished processing
        self.connection.close()


class PostgresInserter(BasePostgresExporter):

    def __init__(self, columns=None, *args, **kwargs):
        """
        Postgres row inserter

        :param columns: List of columns to be updated. By default all fields on the item are updated.
        :param args: Postgres connection args
        :param kwargs: Postgres connection kwargs
        """
        super(PostgresInserter, self).__init__(*args, **kwargs)

        if isinstance(columns, six.string_types):
            columns = [columns]

        if columns:
            self.columns = columns
        else:
            # Get all columns on the table from the database
            self.columns = self.table_columns()

    def __call__(self, item):
        cursor = self.connection.cursor()
        try:
            cursor.execute(self.insert_sql_template(item), self.insert_sql_params(item))
            self.connection.commit()
        except ProgrammingError as e:
            self.connection.rollback()
            self.handle_error(e)
        else:
            return item
        finally:
            cursor.close()

    def insert_sql_template(self, item):
        """
        Build a template for the SQL insert query. Don't interpolate the values
        here as we want Psycopg2 to handle that.
        """
        columns = self.item_columns(item, self.columns)
        sql = 'INSERT into %(table)s (%(column_names)s) values (%(value_markers)s)' % dict(
            table=self.table,
            column_names=','.join(columns),
            value_markers=', '.join(['%s'] * len(columns))
        )
        return sql

    def insert_sql_params(self, item):
        """
        Get a list of query parameters for the SQL query
        """
        columns = self.item_columns(item, self.columns)
        return [item[column] for column in columns]


class PostgresUpdater(BasePostgresExporter):

    def __init__(self, identifiers=None, columns=None, *args, **kwargs):
        """
        Postgres row updater (does not insert if the row does not exist)

        :param identifiers: List of column names used to identify an item as unique (used in SQL where clause)
        :param columns: List of columns to be updated. By default all fields on the item are updated.
        :param args: Postgres connection args
        :param kwargs: Postgres connection kwargs
        """
        super(PostgresUpdater, self).__init__(*args, **kwargs)

        if isinstance(identifiers, six.string_types):
            identifiers = [identifiers]

        if isinstance(columns, six.string_types):
            columns = [columns]

        if columns:
            self.columns = columns
        else:
            # Get all columns on the table from the database
            self.columns = self.table_columns()

        if identifiers:
            self.identifiers = identifiers
        else:
            # By default, try to update on the item's id
            self.identifiers = ['id']

    def __call__(self, item):
        cursor = self.connection.cursor()
        try:
            cursor.execute(self.update_sql_template(item), self.update_sql_params(item))
            self.connection.commit()
        except ProgrammingError as e:
            self.connection.rollback()
            self.handle_error(e)
        else:
            return item
        finally:
            cursor.close()

    def update_sql_template(self, item):
        """
        Build an SQL query template from the column names and identifying columns

        Values aren't inserted here as we want psycopg2 to handle the query param interpolation.
        Psycopg2 will handle converting python types (e.g. None) to SQL compatible values.

        As we're using regular python string interpolation here, there might be some security issues
        if user data is passed as item keys... But that seems very unlikely.
        """
        columns = self.item_columns(item, self.columns, self.identifiers)
        sql = 'UPDATE %(table)s SET %(set_clause)s WHERE %(where_clause)s' % dict(
            table=self.table,
            set_clause=', '.join(["%s = %%s" % col for col in columns if col in item]),
            where_clause=' AND '.join(["%s = %%s" % i for i in self.identifiers])
        )
        return sql

    def update_sql_params(self, item):
        """
        Get a list of query parameters for the SQL query
        """
        columns = self.item_columns(item, self.columns, self.identifiers)
        try:
            set_params = [item[col] for col in columns if col in item]
            where_params = [item[i] for i in self.identifiers]
            params = set_params + where_params
        except KeyError:
            raise ItemNotImportable("""Could not update item in Postgres database as an
                        identifier field was missing from the item.""")
        else:
            return params


class PostgresUpserter(BasePostgresExporter):

    def __init__(self, identifiers=None, columns=None, *args, **kwargs):
        """
        Upsert rows in a Postgres database

        NOTE: this upsert implementation isn't perfect; it is susceptible to some race conditions
            (see: https://www.depesz.com/2012/06/10/why-is-upsert-so-complicated/)

        TODO: batch upsert using Postgres COPY from a csv file
            (example: https://gist.github.com/luke/5697511)

        :param identifiers: List of column names used to identify an item as unique (used in SQL where clause)
        :param columns: List of columns to be updated. By default all fields on the item are updated.
        :param args: Postgres connection args
        :param kwargs: Postgres connection kwargs
        """
        super(PostgresUpserter, self).__init__(*args, **kwargs)

        if isinstance(identifiers, six.string_types):
            identifiers = [identifiers]

        if isinstance(columns, six.string_types):
            columns = [columns]

        if columns:
            self.columns = columns
        else:
            # Get all columns on the table from the database
            self.columns = self.table_columns()

        if identifiers:
            self.identifiers = identifiers
        else:
            # By default, try to update on the item's id
            self.identifiers = ['id']

    def __call__(self, item):
        cursor = self.connection.cursor()

        all_columns = self.item_columns(item, self.columns)
        update_columns = self.item_columns(item, self.columns, self.identifiers)

        where_sql = ' AND '.join('%s = %%s' % i for i in self.identifiers)
        columns_sql = ', '.join(all_columns)
        values_sql = ', '.join(['%s'] * len(all_columns))
        set_sql = ', '.join('%s = %%s' % col for col in update_columns)

        where_params = [item[i] for i in self.identifiers]
        set_params = [item[col] for col in update_columns]
        values_params = [item[col] for col in all_columns]

        select_sql = 'SELECT COUNT(*) FROM %(table)s WHERE %(where_sql)s LIMIT 1' % dict(
            table=self.table,
            where_sql=where_sql
        )
        select_params = where_params

        update_sql = 'UPDATE %(table)s SET %(set_sql)s WHERE %(where_sql)s' % dict(
            table=self.table,
            set_sql=set_sql,
            where_sql=where_sql
        )
        update_params = set_params + where_params

        insert_sql = 'INSERT INTO %(table)s (%(columns_sql)s) VALUES (%(values_sql)s)' % dict(
            table=self.table,
            columns_sql=columns_sql,
            values_sql=values_sql
        )
        insert_params = values_params

        try:
            cursor.execute(select_sql, select_params)

            if cursor.fetchone()[0] > 0:
                # Already in database, update row values
                cursor.execute(update_sql, update_params)
            else:
                # Does not exist, insert the item
                cursor.execute(insert_sql, insert_params)

            # TODO: add a property to the item meta object to indicate if the item was inserted or updated

            self.connection.commit()
        except ProgrammingError as e:
            self.connection.rollback()
            self.handle_error(e)
        else:
            return item
        finally:
            cursor.close()
